"""Convert thousands of WAV files to MP3 without changing source audio.

Double-click batch_wav_to_mp3.bat for the Tkinter GUI, or use:
    python batch_wav_to_mp3.py --source PATH --output PATH [--bitrate 128k]

Requires ffmpeg on PATH (or ffmpeg.exe beside this script / under bin/).
No third-party Python libraries are required.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from typing import Callable

BITRATES = ("96k", "128k", "160k", "192k", "256k", "320k")


@dataclass(frozen=True)
class Conversion:
    source: Path
    destination: Path


@dataclass(frozen=True)
class Result:
    status: str  # converted, skipped, failed, cancelled
    conversion: Conversion
    reason: str = ""


def find_ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    script_folder = Path(__file__).resolve().parent
    for candidate in (script_folder / "ffmpeg.exe", script_folder / "bin" / "ffmpeg.exe"):
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        "FFmpegが見つかりません。ffmpeg.exe をPATHに追加するか、このツールと同じフォルダに配置してください。"
    )


def build_plan(source: Path, output: Path) -> list[Conversion]:
    """Find WAV audio recursively, preserve relative folder structure."""
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"入力フォルダが見つかりません: {source}")
    if output.is_file():
        raise ValueError(f"出力先がフォルダではありません: {output}")
    items = []
    seen = set()
    for path in source.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".wav":
            continue
        relative = path.relative_to(source).with_suffix(".mp3")
        destination = output / relative
        key = str(destination).casefold()
        if key in seen:
            raise ValueError(f"MP3の出力先が重複します: {destination}")
        seen.add(key)
        items.append(Conversion(path, destination))
    return sorted(items, key=lambda c: str(c.source).casefold())


def convert_one(
    item: Conversion,
    ffmpeg: str,
    bitrate: str = "128k",
    overwrite: bool = False,
    cancelled: threading.Event | None = None,
) -> Result:
    if cancelled is not None and cancelled.is_set():
        return Result("cancelled", item)
    if item.destination.exists() and not overwrite:
        return Result("skipped", item, "変換済みMP3が存在します")
    item.destination.parent.mkdir(parents=True, exist_ok=True)
    # Use unique temporary output per file. A failed encoding cannot corrupt an existing MP3.
    partial = item.destination.with_name(f".vds_{uuid.uuid4().hex}.mp3")
    try:
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(item.source), "-map", "0:a:0", "-vn",
            "-codec:a", "libmp3lame", "-b:a", bitrate,
            "-id3v2_version", "3", str(partial),
        ]
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if process.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
            error = (process.stderr or "FFmpegが音声ファイルを生成できませんでした").strip()
            return Result("failed", item, error[-800:])
        # A Stop request does not destroy a completed conversion; it prevents the next file.
        os.replace(partial, item.destination)
        return Result("converted", item)
    except (OSError, subprocess.SubprocessError) as exc:
        return Result("failed", item, str(exc))
    finally:
        partial.unlink(missing_ok=True)


def convert_batch(
    plan: list[Conversion],
    *,
    ffmpeg: str,
    bitrate: str = "128k",
    workers: int = 2,
    overwrite: bool = False,
    cancelled: threading.Event | None = None,
    on_result: Callable[[int, int, Result], None] | None = None,
) -> dict[str, int]:
    if bitrate not in BITRATES:
        raise ValueError("非対応のビットレートです")
    if not 1 <= workers <= 8:
        raise ValueError("並列数は1～8の範囲にしてください")
    stop = cancelled if cancelled is not None else threading.Event()
    stats = {"converted": 0, "skipped": 0, "failed": 0, "cancelled": 0}
    # Bound submitted tasks to avoid holding thousands of futures for massive projects.
    iterator = iter(plan)
    pending: set[concurrent.futures.Future[Result]] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        def refill() -> None:
            while len(pending) < workers and not stop.is_set():
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                pending.add(pool.submit(convert_one, item, ffmpeg, bitrate, overwrite, stop))

        refill()
        completed = 0
        while pending:
            done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                result = future.result()
                stats[result.status] += 1
                completed += 1
                if on_result:
                    on_result(completed, len(plan), result)
            refill()
    # Unstarted files are cancelled too, so counts always add up to total.
    remaining = len(plan) - sum(stats.values())
    if remaining:
        stats["cancelled"] += remaining
    return stats


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("VoiceDramaStudio | WAV → MP3 一括変換")
    root.geometry("850x630")
    root.minsize(730, 510)
    root.columnconfigure(1, weight=1)
    root.rowconfigure(8, weight=1)

    source_var = tk.StringVar()
    output_var = tk.StringVar()
    bitrate_var = tk.StringVar(value="128k")
    workers_var = tk.StringVar(value="2")
    overwrite_var = tk.BooleanVar(value=False)
    progress_var = tk.DoubleVar(value=0)
    status_var = tk.StringVar(value="入力フォルダを選択してください。WAVは変更されません。")
    stop = threading.Event()
    updates: queue.Queue[tuple] = queue.Queue()
    running = False

    def choose_source():
        chosen = filedialog.askdirectory(title="WAVが入ったフォルダを選択")
        if chosen:
            source_var.set(chosen)
            if not output_var.get().strip():
                p = Path(chosen)
                output_var.set(str(p.with_name(p.name + "_mp3")))

    def choose_output():
        chosen = filedialog.askdirectory(title="MP3の保存先を選択")
        if chosen:
            output_var.set(chosen)

    ttk.Label(root, text="WAV → MP3 大量一括変換", font=("Yu Gothic UI", 16, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(20, 14)
    )
    ttk.Label(root, text="入力（WAVのルート）").grid(row=1, column=0, sticky="w", padx=(18, 8), pady=8)
    ttk.Entry(root, textvariable=source_var).grid(row=1, column=1, sticky="ew", pady=8)
    ttk.Button(root, text="参照…", command=choose_source).grid(row=1, column=2, padx=18, pady=8)
    ttk.Label(root, text="保存先（MP3のルート）").grid(row=2, column=0, sticky="w", padx=(18, 8), pady=8)
    ttk.Entry(root, textvariable=output_var).grid(row=2, column=1, sticky="ew", pady=8)
    ttk.Button(root, text="参照…", command=choose_output).grid(row=2, column=2, padx=18, pady=8)

    settings = ttk.Frame(root)
    settings.grid(row=3, column=0, columnspan=3, sticky="w", padx=18, pady=9)
    ttk.Label(settings, text="音質").pack(side="left", padx=(0, 8))
    ttk.Combobox(settings, textvariable=bitrate_var, values=BITRATES, state="readonly", width=9).pack(side="left")
    ttk.Label(settings, text="同時変換数").pack(side="left", padx=(25, 8))
    ttk.Combobox(settings, textvariable=workers_var, values=["1", "2", "3", "4"], state="readonly", width=5).pack(side="left")
    ttk.Checkbutton(settings, text="同名MP3を上書きする", variable=overwrite_var).pack(side="left", padx=25)

    ttk.Label(root, text="サブフォルダ構造を維持します。既存のMP3は標準ではスキップします。ゲームでは data/sound/voice/ に配置します。",
              wraplength=790, foreground="#476174").grid(row=4, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 10))
    actions = ttk.Frame(root)
    actions.grid(row=5, column=0, columnspan=3, sticky="w", padx=18, pady=(3, 11))
    start_button = ttk.Button(actions, text="▶ 変換開始")
    start_button.pack(side="left")
    stop_button = ttk.Button(actions, text="■ 中断", state="disabled")
    stop_button.pack(side="left", padx=8)
    ttk.Progressbar(root, variable=progress_var, maximum=100).grid(row=6, column=0, columnspan=3, sticky="ew", padx=18)
    ttk.Label(root, textvariable=status_var, wraplength=780).grid(row=7, column=0, columnspan=3, sticky="w", padx=18, pady=9)
    logs = scrolledtext.ScrolledText(root, height=16, wrap="word", state="disabled")
    logs.grid(row=8, column=0, columnspan=3, sticky="nsew", padx=18, pady=(3, 18))
    loglines = 0

    def log(text: str):
        nonlocal loglines
        logs.configure(state="normal")
        logs.insert("end", text + "\n")
        loglines += 1
        if loglines > 400:
            logs.delete("1.0", "101.0")
            loglines -= 100
        logs.see("end")
        logs.configure(state="disabled")

    def set_busy(active: bool):
        nonlocal running
        running = active
        start_button.configure(state="disabled" if active else "normal")
        stop_button.configure(state="normal" if active else "disabled")

    def start():
        if running:
            return
        try:
            source = Path(source_var.get().strip())
            output = Path(output_var.get().strip())
            if not source_var.get().strip() or not output_var.get().strip():
                raise ValueError("入力フォルダと保存先を指定してください")
            plan = build_plan(source, output)
            if not plan:
                raise ValueError("入力フォルダにWAVがありません")
            ffmpeg = find_ffmpeg()
            bitrate = bitrate_var.get()
            workers = int(workers_var.get())
            overwrite = overwrite_var.get()
        except (ValueError, OSError) as exc:
            messagebox.showerror("変換できません", str(exc), parent=root)
            return
        progress_var.set(0)
        stop.clear()
        set_busy(True)
        log(f"変換対象: {len(plan):,}ファイル / 保存先: {output}")
        status_var.set(f"0 / {len(plan):,} 件")

        def run():
            try:
                stats = convert_batch(
                    plan, ffmpeg=ffmpeg, bitrate=bitrate, workers=workers,
                    overwrite=overwrite, cancelled=stop,
                    on_result=lambda n, total, result: updates.put(("item", n, total, result)),
                )
                updates.put(("done", stats))
            except Exception as exc:
                updates.put(("error", str(exc)))

        threading.Thread(target=run, daemon=True).start()

    def cancel():
        stop.set()
        status_var.set("中断を要求しました。処理中のファイルが完了した後に停止します…")
        stop_button.configure(state="disabled")

    start_button.configure(command=start)
    stop_button.configure(command=cancel)

    def poll():
        while True:
            try:
                payload = updates.get_nowait()
            except queue.Empty:
                break
            if payload[0] == "item":
                _, n, total, result = payload
                progress_var.set(n * 100 / total)
                status_var.set(f"{n:,} / {total:,} 件処理済み")
                if result.status == "failed":
                    log(f"エラー: {result.conversion.source} — {result.reason}")
            elif payload[0] == "done":
                stats = payload[1]
                set_busy(False)
                progress_var.set(100)
                summary = (f"変換 {stats['converted']:,}件 / スキップ {stats['skipped']:,}件 / "
                           f"失敗 {stats['failed']:,}件 / 未処理 {stats['cancelled']:,}件")
                status_var.set(summary)
                log(summary)
                messagebox.showinfo("一括変換終了", summary, parent=root)
            else:
                set_busy(False)
                log(payload[1])
                messagebox.showerror("処理エラー", payload[1], parent=root)
        root.after(150, poll)

    root.after(150, poll)
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recursively convert WAV files to MP3 without touching originals")
    parser.add_argument("--source", type=Path, help="Folder containing WAV files")
    parser.add_argument("--output", type=Path, help="Destination root; folder hierarchy is preserved")
    parser.add_argument("--bitrate", choices=BITRATES, default="128k")
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 9))
    parser.add_argument("--overwrite", action="store_true", help="Replace existing MP3 only after successful encoding")
    args = parser.parse_args(argv)
    if args.source is None and args.output is None:
        run_gui()
        return 0
    if args.source is None or args.output is None:
        parser.error("--source and --output must be specified together")
    try:
        ffmpeg = find_ffmpeg()
        plan = build_plan(args.source, args.output)
        print(f"WAV files: {len(plan):,}")
        if not plan:
            return 0
        def show(n: int, total: int, result: Result) -> None:
            if result.status == "failed":
                print(f"ERROR {result.conversion.source}: {result.reason}", file=sys.stderr)
            if n % 50 == 0 or n == total:
                print(f"Progress: {n:,}/{total:,}", flush=True)
        stats = convert_batch(plan, ffmpeg=ffmpeg, bitrate=args.bitrate, workers=args.workers,
                              overwrite=args.overwrite, on_result=show)
        print("Result:", stats)
        return 1 if stats["failed"] else 0
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

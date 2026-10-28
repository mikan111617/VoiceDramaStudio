/* VoiceDrama Studio -> pixel timeline bridge. Local origin only. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const base = '/pixel/api/vds';
  const select = (node, entries, placeholder) => {
    node.replaceChildren(new Option(placeholder, ''));
    for (const item of entries) node.add(new Option(item.label, item.value));
  };
  const request = async (url, options) => {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || '読み込みに失敗しました');
    return body;
  };
  async function loadProjects() {
    const data = await request(base + '/projects');
    select($('vdsProject'), (data.projects || []).map(x => ({value:x.id, label:x.title})), '作品を選択');
    const from = new URLSearchParams(location.search).get('project');
    if (from && [...$('vdsProject').options].some(x => x.value === from)) $('vdsProject').value = from;
    await loadScenes();
  }
  async function loadScenes() {
    select($('vdsScene'), [], 'シーンを選択');
    if (!$('vdsProject').value) return;
    const data = await request(base + '/projects/' + encodeURIComponent($('vdsProject').value) + '/scenes');
    select($('vdsScene'), (data.scenes || []).map(x => ({value:x.id, label:x.name})), 'シーンを選択');
    const from = new URLSearchParams(location.search).get('scene');
    if (from && [...$('vdsScene').options].some(x => x.value === from)) $('vdsScene').value = from;
  }
  async function loadTargets() {
    const data = await request('/pixel/api/sequences');
    select($('vdsTargetSeq'), (data.sequences || []).map(x => ({value:x.id,label:x.name + '（追加）'})), '新しいシーケンスを作成');
  }
  async function importScene() {
    const project_id = $('vdsProject').value, scene_id = $('vdsScene').value;
    if (!project_id || !scene_id) throw new Error('作品とシーンを選んでください');
    const target = $('vdsTargetSeq').value;
    const body = {project_id, scene_id, sequence_id: target || null,
      fps: Number(document.getElementById('sequenceFps')?.value || 12),
      width: Number(document.getElementById('sequenceWidth')?.value || 240),
      height: Number(document.getElementById('sequenceHeight')?.value || 160)};
    const button = $('vdsImportBtn');
    button.disabled = true;
    $('vdsImportStatus').textContent = '元の音声素材をコピーし、開始位置と長さを映像タイムラインへ変換しています…';
    try {
      const r = await request(base + '/import-scene', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      $('vdsImportStatus').textContent = r.clips_added + '件を追加しました。元の音声は変更していません。' + (r.skipped?.length ? (' スキップ: ' + r.skipped.join(', ')) : '');
      if (typeof loadAssets === 'function') await loadAssets();
      if (typeof loadSequences === 'function') {
        currentSequenceId = r.sequence_id;
        await loadSequences();
        await openSequence(r.sequence_id);
      }
      await loadTargets();
    } finally {button.disabled = false;}
  }
  document.addEventListener('DOMContentLoaded', async () => {
    if (!$('vdsImportBtn')) return;
    $('vdsProject').addEventListener('change', () => loadScenes().catch(e => $('vdsImportStatus').textContent = e.message));
    $('vdsImportBtn').addEventListener('click', () => importScene().catch(e => $('vdsImportStatus').textContent = '取り込み失敗: ' + e.message));
    try { await Promise.all([loadProjects(), loadTargets()]); }
    catch (e) { $('vdsImportStatus').textContent = 'VoiceDramaに接続できません: ' + e.message; }
  });
})();

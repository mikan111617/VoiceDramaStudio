"""MCP stdio server exposing constrained Aseprite sprite operations.

Start with: python aseprite_mcp_server.py
Use an MCP-capable client; VoiceDrama Studio itself does not require one.
"""
from __future__ import annotations
import json
import sys

from aseprite_bridge import add_layers, create_sprite, export_sprite, list_sprites, status

TOOLS = [
    {"name": "aseprite_status", "description": "Check local Steam Aseprite connection.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "aseprite_list", "description": "List locally saved native Aseprite sprites.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "aseprite_create",
     "description": "Draw .aseprite layers and frames. Shapes are pixel,rect,line,ellipse; "
                    "colors are #RRGGBB; no arbitrary code or Lua.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"},
         "frames": {"type": "integer"}, "layers": {"type": "array"},
         "commands": {"type": "array"}}, "required": ["width", "height"]}},
    {"name": "aseprite_add_layers",
     "description": "Add layers to an existing sprite without replacing old layers.",
     "inputSchema": {"type": "object", "properties": {
         "sprite_id": {"type": "string"}, "layers": {"type": "array"},
         "commands": {"type": "array"}}, "required": ["sprite_id"]}},
    {"name": "aseprite_export", "description": "Export PNG, GIF, or spritesheet.",
     "inputSchema": {"type": "object", "properties": {
         "sprite_id": {"type": "string"},
         "format": {"type": "string", "enum": ["png", "gif", "sheet"]}},
         "required": ["sprite_id", "format"]}},
]


def dispatch(method, params):
    if method == "initialize":
        return {"protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "voicedrama-aseprite", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method != "tools/call":
        raise LookupError("Unknown method")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    name = params.get("name")
    if name == "aseprite_status":
        result = status()
    elif name == "aseprite_list":
        result = list_sprites()
    elif name == "aseprite_create":
        result = create_sprite(args)
    elif name == "aseprite_add_layers":
        result = add_layers(args["sprite_id"], args)
    elif name == "aseprite_export":
        result = {"path": str(export_sprite(args["sprite_id"], args["format"]))}
    else:
        raise ValueError("Unknown Aseprite tool")
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}


def serve(stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        if not line.strip():
            continue
        request = None
        try:
            request = json.loads(line)
            if request.get("method") in ("notifications/initialized", "notifications/cancelled"):
                continue
            result = dispatch(request.get("method"), request.get("params") or {})
            if "id" not in request:
                continue
            message = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as exc:
            rid = request.get("id") if isinstance(request, dict) else None
            if isinstance(request, dict) and request.get("method") == "tools/call":
                message = {"jsonrpc": "2.0", "id": rid,
                           "result": {"content": [{"type": "text", "text": str(exc)[:400]}],
                                      "isError": True}}
            else:
                message = {"jsonrpc": "2.0", "id": rid,
                           "error": {"code": -32601 if isinstance(exc, LookupError) else -32602,
                                     "message": str(exc)[:400]}}
        stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        stdout.flush()


if __name__ == "__main__":
    serve()

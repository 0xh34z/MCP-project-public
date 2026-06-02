import os
import time
import json
import re
import html
import traceback
import asyncio
import mysql.connector
import requests
import datetime
import urllib.request
from typing import List, Dict, Any, Optional, Callable

# For MCP integration
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import logging

# Local nmap compactor (supports both package and direct-script execution)
try:
    from .nmap_compactor import parse_nmap_to_json, nmap_json_to_summary
except Exception:
    from nmap_compactor import parse_nmap_to_json, nmap_json_to_summary

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Standard reasoning/thinking tags borrowed from Open WebUI patterns
REASONING_TAGS = [
    (r'<think>', r'</think>'),
    (r'<thinking>', r'</thinking>'),
    (r'<reason>', r'</reason>'),
    (r'<reasoning>', r'</reasoning>'),
    (r'<thought>', r'</thought>'),
    (r'<Thought>', r'</Thought>'),
    (r'<\|begin_of_thought\|>', r'<\|end_of_thought\|>'),
    (r'◁think▷', r'◁/think▷'),
]


class JobTerminatedError(RuntimeError):
    """Raised when a running job has been terminated by the user."""


def get_job_status(job_id: int) -> str:
    conn = db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT status FROM jobs WHERE id = %s LIMIT 1", (int(job_id),))
        row = cur.fetchone()
        return str((row[0] if row and row[0] is not None else '')).strip().lower()
    finally:
        cur.close()
        conn.close()


def ensure_job_running(job_id: int) -> None:
    status = get_job_status(job_id)
    if status != 'running':
        raise JobTerminatedError(f"Job {job_id} is no longer running (status={status or 'unknown'})")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def strip_context_noise(content: str, include_tool_traces: bool = False) -> str:
    """Remove live trace/thought noise so context focuses on conversational intent."""
    text = str(content or '')

    if not include_tool_traces:
        text = re.sub(r'<div class="tool-trace-card">[\s\S]*?</div>\s*', '', text, flags=re.IGNORECASE)

    # Remove markdown status blocks emitted by worker, e.g. > [!THOUGHT] ...
    cleaned_lines: List[str] = []
    skip_quote_block = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('> [!THOUGHT]') or stripped.startswith('> [!TOOL]'):
            skip_quote_block = True
            continue
        if skip_quote_block:
            if stripped.startswith('>') or stripped == '':
                continue
            skip_quote_block = False
        cleaned_lines.append(line)

    text = '\n'.join(cleaned_lines)

    # Drop any remaining HTML tags before sending to LLM.
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def build_compact_summary(messages: List[Dict[str, str]], max_chars: int) -> str:
    """Create a deterministic compact summary of older context without extra LLM cost."""
    if not messages:
        return ''

    lines: List[str] = []
    used = 0
    for item in messages:
        role = str(item.get('role') or 'user')
        content = str(item.get('content') or '').strip()
        if not content:
            continue
        snippet = content.replace('\n', ' ')
        if len(snippet) > 220:
            snippet = snippet[:220].rstrip() + '...'
        line = f"- {role}: {snippet}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1

    if not lines:
        return ''

    return "Conversation summary (older context):\n" + "\n".join(lines)


def _pretty_json_text(value: Any) -> str:
    """Serialize to readable JSON, falling back to string representation."""
    try:
        if isinstance(value, str):
            parsed = json.loads(value)
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        return json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:
        return str(value)


def repair_mojibake_text(text: str) -> str:
    """Attempt to repair UTF-8 text that was decoded as Latin-1/CP1252."""
    source = str(text or '')
    if not source:
        return source

    # Heuristic: only attempt repair when common mojibake markers appear.
    marker_count = sum(source.count(ch) for ch in ('Ã', 'Â', 'ð', 'â', '�'))
    if marker_count == 0:
        return source

    try:
        repaired = source.encode('latin-1', errors='strict').decode('utf-8', errors='strict')
    except Exception:
        return source

    repaired_markers = sum(repaired.count(ch) for ch in ('Ã', 'Â', 'ð', 'â', '�'))
    if repaired_markers < marker_count:
        return repaired
    return source


def auto_close_thinking_tags(text: str) -> str:
    """If a reasoning block is opened but not closed, and is followed by a tool call or final content indicators, close it."""
    result = str(text or '')
    for start, end in REASONING_TAGS:
        start_pat = re.compile(re.escape(start), re.IGNORECASE)
        end_pat = re.compile(re.escape(end), re.IGNORECASE)
        
        start_matches = list(start_pat.finditer(result))
        if not start_matches:
            continue
            
        if not end_pat.search(result):
            start_idx = start_matches[-1].end()
            remaining_text = result[start_idx:]
            
            # Look for indicators of tool calls
            fence_match = re.search(r'```', remaining_text)
            gemma_match = re.search(r'<\|tool\|>', remaining_text)
            json_match = re.search(r'\{\s*"tool"\s*:', remaining_text)
            
            split_idx = -1
            for m in (fence_match, gemma_match, json_match):
                if m:
                    idx = m.start()
                    if split_idx == -1 or idx < split_idx:
                        split_idx = idx
                        
            if split_idx != -1:
                insert_pos = start_idx + split_idx
                # Cleanly insert the closing tag right before the tool call
                result = result[:insert_pos] + f"\n{end}\n" + result[insert_pos:]
    return result


def sanitize_assistant_visible_text(text: str) -> str:
    """Strip reasoning wrappers and normalize text for end-user display."""
    cleaned = str(text or '')
    
    # Auto-close reasoning blocks if followed by tool call JSON to avoid stripping JSON
    cleaned = auto_close_thinking_tags(cleaned)
    
    # Strip reasoning blocks (both closed and unclosed)
    for start, end in REASONING_TAGS:
        # 1. Strip closed blocks
        pattern_closed = re.compile(f'{start}[\\s\\S]*?{end}', re.IGNORECASE)
        cleaned = pattern_closed.sub('', cleaned)
        # 2. Strip unclosed blocks (opened but no closing tag found)
        pattern_open = re.compile(f'{start}[\\s\\S]*$', re.IGNORECASE)
        cleaned = pattern_open.sub('', cleaned)

    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    cleaned = repair_mojibake_text(cleaned)
    return cleaned


def compact_nmap_output(raw_output: str, max_hosts: int = 30) -> str:
    """Summarize nmap -sn style output so the LLM can reason over it efficiently."""
    text = str(raw_output or '').strip()
    if not text:
        return '(empty nmap output)'

    def _is_truncated_output(value: str) -> bool:
        lowered = value.lower()
        return any(
            marker in lowered
            for marker in (
                '... [truncated',
                '[error] command timed out',
                'results above may be incomplete',
                'timed out after',
                'partial_results',
            )
        )

    host_blocks: List[Dict[str, Any]] = []
    current_host: Optional[str] = None
    current_lines: List[str] = []

    for line in text.splitlines():
        host_match = re.match(r'Nmap scan report for\s+(.+)', line)
        if host_match:
            if current_host is not None:
                host_blocks.append({'host': current_host, 'lines': current_lines})
            current_host = host_match.group(1).strip()
            current_lines = [line.rstrip()]
            continue

        if current_host is not None:
            current_lines.append(line.rstrip())

    if current_host is not None:
        host_blocks.append({'host': current_host, 'lines': current_lines})

    unique_hosts: List[Dict[str, Any]] = []
    seen = set()
    for block in host_blocks:
        host = str(block.get('host') or '').strip()
        if not host:
            continue
        key = host.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_hosts.append(block)

    done_match = re.search(r'Nmap done:\s+(\d+)\s+IP addresses\s+\((\d+)\s+hosts up\)', text)
    scanned_count = int(done_match.group(1)) if done_match else None
    up_count = int(done_match.group(2)) if done_match else len(unique_hosts)

    lines = []
    if scanned_count is not None:
        lines.append(f"Nmap summary: scanned={scanned_count}, up={up_count}")
    else:
        lines.append(f"Nmap summary: detected_up_hosts={len(unique_hosts)}")

    lines.append(
        "Authoritative note: only hosts and services listed below are confirmed. "
        "Do not infer additional hosts, ports, or services beyond the visible output."
    )

    if unique_hosts:
        preview = unique_hosts[:max_hosts]
        lines.append("Up hosts (first entries):")
        for block in preview:
            host = str(block.get('host') or '').strip()
            lines.append(f"- {host}")
            block_lines = [str(item).strip() for item in block.get('lines') or [] if str(item).strip()]
            for item in block_lines[1:]:
                if (
                    item.startswith('Host is up')
                    or item.startswith('PORT ')
                    or re.match(r'^\d+/(tcp|udp)\s+', item)
                    or item.startswith('MAC Address:')
                    or item.startswith('Service Info:')
                    or item.startswith('Not shown:')
                ):
                    lines.append(f"  - {item}")
        remaining = len(unique_hosts) - len(preview)
        if remaining > 0:
            lines.append(f"- ... and {remaining} more hosts")

    if _is_truncated_output(text):
        lines.append("Note: The original Nmap output was truncated or incomplete.")

    return '\n'.join(lines).strip()


def compact_tool_result_for_llm(tool_name: str, raw_result: str, max_chars: int = 4500) -> str:
    """Reduce noisy tool output before feeding back into the LLM loop."""
    text = str(raw_result or '').strip()
    if not text:
        return '(empty tool output)'

    wrapper_stdout = text
    wrapper_stderr = ''
    parsed = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            wrapper_stdout = str(parsed.get('stdout') or '').strip() or text
            wrapper_stderr = str(parsed.get('stderr') or '').strip()
    except Exception:
        pass

    if wrapper_stdout != text and wrapper_stderr:
        text = f"{wrapper_stdout}\n\n[stderr]\n{wrapper_stderr}".strip()
    else:
        text = wrapper_stdout

    lower_tool = str(tool_name or '').strip().lower()
    
    # Specialized summarizer for Nmap
    # Produce a structured JSON host table plus short human summary
    if 'nmap' in lower_tool:
        try:
            if isinstance(parsed, dict) and isinstance(parsed.get('nmap'), dict):
                nmap_data = parsed['nmap']
                result_obj = {
                    'nmap': nmap_data,
                    'summary': nmap_json_to_summary(nmap_data, max_hosts=50),
                }
                result_text = json.dumps(result_obj, indent=2, ensure_ascii=False)
                if len(result_text) <= max_chars:
                    return result_text

            parsed = parse_nmap_to_json(text)
            result_obj = {'nmap': parsed, 'summary': nmap_json_to_summary(parsed, max_hosts=50)}
            result_text = json.dumps(result_obj, indent=2, ensure_ascii=False)

            if len(result_text) <= max_chars:
                return result_text

            # If still too big, progressively reduce hosts list to fit
            original_hosts = parsed.get('hosts', [])
            for keep in (50, 20, 10, 5, 1):
                truncated_parsed = dict(parsed)
                truncated_parsed['hosts'] = original_hosts[:keep]
                truncated_obj = {'nmap': truncated_parsed, 'summary': nmap_json_to_summary(truncated_parsed, max_hosts=keep)}
                candidate = json.dumps(truncated_obj, indent=2, ensure_ascii=False)
                if len(candidate) <= max_chars:
                    return candidate

            # Fallback to a trimmed text with explicit truncation note
            head = result_text[: max_chars // 2].rstrip()
            tail = result_text[-(max_chars // 3) :].lstrip()
            omitted = len(result_text) - len(head) - len(tail)
            return (
                f"{head}\n\n... [truncated {omitted} chars] ...\n\n{tail}\n\n"
                "Note: The tool output was truncated. Do not infer omitted hosts, ports, files, or values."
            )
        except Exception:
            # Fall back to the legacy compact prose summarizer
            summarized = compact_nmap_output(text)
            if summarized:
                return summarized[:max_chars]

    # Specialized summarizer for Proxmox lists (VMs/Nodes)
    # Only summarize if it's actually huge (> 10,000 chars)
    if 'proxmox' in lower_tool and ('list' in lower_tool or 'get' in lower_tool) and len(text) > 10000:
        try:
            # If it's a JSON array of dicts, take first 100 and only keep key fields
            data = json.loads(text)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                compacted_list = []
                # Priority fields to keep
                keep_keys = {'vmid', 'name', 'status', 'node', 'type', 'id', 'ip', 'address'}
                for item in data[:100]: # Show up to 100 items
                    compacted_item = {k: v for k, v in item.items() if k.lower() in keep_keys}
                    compacted_list.append(compacted_item)
                
                result_text = json.dumps(compacted_list, indent=2)
                if len(data) > 100:
                    result_text += f"\n... [truncated {len(data)-100} more items] ..."
                return result_text[:max_chars]
        except Exception:
            pass

    if len(text) <= max_chars:
        return text

    head = text[: max_chars // 2].rstrip()
    tail = text[-(max_chars // 3) :].lstrip()
    omitted = len(text) - len(head) - len(tail)
    return (
        f"{head}\n\n... [truncated {omitted} chars] ...\n\n{tail}\n\n"
        "Note: The tool output was truncated. Do not infer omitted hosts, ports, files, or values."
    )


def should_force_plain_summary(reply: str, had_tool_calls: bool) -> bool:
    """Detect cases where model did not provide a final natural-language answer."""
    if not had_tool_calls:
        return False

    cleaned = sanitize_assistant_visible_text(reply)
    if not cleaned:
        return True

    if extract_tool_call(reply) is not None:
        return True

    too_json_like = cleaned.startswith('{') and cleaned.endswith('}') and '"tool"' in cleaned
    if too_json_like:
        return True

    return False


def extract_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extract all valid tool-call JSON objects from model output."""
    if not text:
        return []

    # Do NOT strip thinking blocks before extracting tool calls, as the tool call
    # might be inside or right after an unclosed thinking block.
    # Instead, we just unescape HTML and normalize quotes on the raw text.
    cleaned_text = html.unescape(text)
    cleaned_text = (
        cleaned_text
        .replace('“', '"')
        .replace('”', '"')
        .replace('‘', "'")
        .replace('’', "'")
    )

    found_tools: List[Dict[str, Any]] = []
    
    # 1. Look for fenced JSON blocks
    for match in re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_text, flags=re.IGNORECASE):
        try:
            parsed = json.loads(match.strip())
            if isinstance(parsed, dict) and parsed.get('tool'):
                found_tools.append(parsed)
        except Exception:
            continue

    # 2. Support for Gemma 4 native tool tokens <|tool|>...<|/tool|>
    for match in re.findall(r"<\|tool\|>([\s\S]*?)<\|/tool\|>", cleaned_text, flags=re.IGNORECASE):
        try:
            parsed = json.loads(match.strip())
            if isinstance(parsed, dict) and parsed.get('tool'):
                found_tools.append(parsed)
        except Exception:
            continue

    # 3. Scan for any raw { ... } objects if the above didn't find everything
    # We use a set of strings to avoid adding the same block multiple times
    seen_raw = set()
    decoder = json.JSONDecoder()
    for idx in range(len(cleaned_text)):
        if cleaned_text[idx] != '{':
            continue
        try:
            parsed_obj, end = decoder.raw_decode(cleaned_text[idx:])
            raw_segment = cleaned_text[idx:idx+end]
            if raw_segment in seen_raw:
                continue
            seen_raw.add(raw_segment)
            
            if isinstance(parsed_obj, dict) and parsed_obj.get('tool'):
                # Check if this object was already captured by fenced blocks (simple substring check)
                is_duplicate = any(raw_segment in str(t) for t in found_tools)
                if not is_duplicate:
                    found_tools.append(parsed_obj)
        except Exception:
            continue

    # Clean up arguments for all found tools
    for tool in found_tools:
        args = tool.get('arguments')
        if not isinstance(args, dict):
            # Check if there are top-level arguments instead of nested under 'arguments'
            top_level_args = {k: v for k, v in tool.items() if k not in ('tool', 'arguments')}
            if top_level_args:
                tool['arguments'] = top_level_args
            else:
                tool['arguments'] = {}
            args = tool['arguments']
        
        # Normalize common model escaping hallucinations (e.g. \" inside a shell string)
        for k, v in args.items():
            if isinstance(v, str):
                v = v.replace('\\"', '"').replace("\\'", "'")
                args[k] = v

    return found_tools


def extract_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """Helper to extract a single tool call (first found)."""
    calls = extract_tool_calls(text)
    return calls[0] if calls else None


def build_tool_output_message(
    tool_name: str,
    raw_result: str,
    arguments: Optional[Dict[str, Any]] = None,
    server_name: str = 'unknown',
    duration_ms: Optional[int] = None,
) -> str:
    """Format tool output for storage and UI rendering."""
    pretty_output = _pretty_json_text((raw_result or '').strip() or '(empty tool output)')
    pretty_input = _pretty_json_text(arguments or {})
    duration_label = f"{duration_ms} ms" if duration_ms is not None else "unknown"
    tool_safe = html.escape(tool_name)
    server_safe = html.escape(server_name)
    duration_safe = html.escape(duration_label)
    input_safe = html.escape(pretty_input)
    output_safe = html.escape(pretty_output)

    return (
        '<div class="tool-trace-card">'
        '  <div class="tool-trace-head">'
        f'    <span class="tool-trace-title">Tool Call: {tool_safe}</span>'
        '    <span class="tool-trace-state">completed</span>'
        '  </div>'
        '  <div class="tool-trace-meta">'
        f'    <span>Server: {server_safe}</span>'
        f'    <span>Duration: {duration_safe}</span>'
        '  </div>'
        '  <details class="tool-trace-section">'
        '    <summary>Input</summary>'
        f'    <pre><code class="language-json">{input_safe}</code></pre>'
        '  </details>'
        '  <details class="tool-trace-section" open>'
        '    <summary>Output</summary>'
        f'    <pre><code class="language-json">{output_safe}</code></pre>'
        '  </details>'
        '</div>'
    )


def build_tool_running_message(
    tool_name: str,
    server_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> str:
    """Format tool execution state as a running collapsible UI card."""
    pretty_input = _pretty_json_text(arguments or {})
    tool_safe = html.escape(tool_name)
    server_safe = html.escape(server_name)
    input_safe = html.escape(pretty_input)

    return (
        '<div class="tool-trace-card" style="border-color: #5a6b84; box-shadow: 0 0 0 1px rgba(136, 170, 220, 0.15) inset;">'
        '  <div class="tool-trace-head">'
        f'    <span class="tool-trace-title">Tool Call: {tool_safe}</span>'
        '    <span class="tool-trace-state" style="background-color: rgba(90, 107, 132, 0.15); color: #88ccff; border-color: rgba(90, 107, 132, 0.3);">running</span>'
        '  </div>'
        '  <div class="tool-trace-meta">'
        f'    <span>Server: {server_safe}</span>'
        '  </div>'
        '  <details class="tool-trace-section" open>'
        '    <summary>Input</summary>'
        f'    <pre><code class="language-json">{input_safe}</code></pre>'
        '  </details>'
        '</div>'
    )


def extract_pre_tool_thought(reply: str) -> str:
    """Extract the leading plain-English preamble before a tool call, if any."""
    text = str(reply or '').strip()
    if not text:
        return ''

    lines = text.splitlines()
    thought_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        # Stop if we hit a JSON block, a codeblock, or a tool signature
        if stripped.startswith('{') or stripped.startswith('```') or stripped.startswith('"tool"'):
            break
        thought_lines.append(line)

    thought = '\n'.join(thought_lines).strip()
    if len(thought) > 1500:
        thought = thought[:1500].rstrip() + '...'
    return thought


def looks_like_internal_reasoning(text: str) -> bool:
    """Return True when text appears to be internal chain-of-thought style content."""
    sample = str(text or '').strip().lower()
    if not sample:
        return False

    user_facing_markers = (
        "i'd be happy to help",
        'i can help you',
        'let me know',
        'what would you like',
        'which machine',
        'which tool',
        'however, i need some clarification',
    )
    if any(marker in sample for marker in user_facing_markers):
        return False

    internal_markers = (
        'the user is asking',
        'the user wants',
        'the user requested',
        'the user needs',
        'i should',
        'i need to',
        'i will first',
        'let me think',
        'plan:',
        'reasoning:',
        'i can call',
        'before i',
        'first, i',
        'next, i',
        'step 1',
    )
    return any(marker in sample for marker in internal_markers)


def extract_thinking_block(reply: str) -> str:
    """Extract the model's explicit <think>...</think> block or variants, if present."""
    text = str(reply or '')
    for start, end in REASONING_TAGS:
        # Try closed match first
        match = re.search(f'{start}([\\s\\S]*?){end}', text, flags=re.IGNORECASE)
        if match:
            thought = str(match.group(1) or '').strip()
            return thought if len(thought) < 4000 else thought[:4000] + '...'
            
        # Try unclosed match if no closing tag exists
        if re.search(start, text, flags=re.IGNORECASE):
            match_open = re.search(f'{start}([\\s\\S]*)$', text, flags=re.IGNORECASE)
            if match_open:
                thought = str(match_open.group(1) or '').strip()
                return thought if len(thought) < 4000 else thought[:4000] + '...'
                
    return ''


def parse_prompt_attachments(prompt: str) -> tuple[list[dict[str, Any]], str]:
    """Extract attachment blocks from a prompt and return them with the remaining text."""
    source = str(prompt or '')
    attachments: list[dict[str, Any]] = []
    pattern = re.compile(
        r'\[ATTACHMENT name="([^"]*)" type="([^"]*)" size="([^"]*)"(?: encoding="([^"]*)")?(?: binary="([^"]*)")?(?: truncated="([^"]*)")?\]\n([\s\S]*?)\n\[\/ATTACHMENT\]',
        flags=re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        attachments.append({
            'name': match.group(1) or '',
            'type': match.group(2) or '',
            'size': _safe_int(match.group(3), 0),
            'encoding': (match.group(4) or 'text').lower(),
            'binary': (match.group(5) or '') == '1',
            'truncated': (match.group(6) or '') == '1',
            'content': match.group(7) or '',
        })
        return '\n'

    stripped = pattern.sub(replace, source)
    text = re.sub(r'\[TEXT\]\n([\s\S]*?)\n\[\/TEXT\]', r'\1', stripped, flags=re.IGNORECASE).strip()
    return attachments, text


def looks_like_deploy_request(text: str) -> bool:
    """Return True when a prompt is asking to deploy or host an uploaded app."""
    haystack = str(text or '').lower()
    # Match word prefixes so "hosting", "deploying", "launching" etc. all count.
    return bool(re.search(r'\b(deploy|host|run|launch|spin\s*up|publish|start|serve|upload|webserver|website|lxc|container|nginx|apache)', haystack))


def is_zip_attachment(attachment: Dict[str, Any]) -> bool:
    name = str(attachment.get('name') or '').lower()
    mime = str(attachment.get('type') or '').lower()
    encoding = str(attachment.get('encoding') or '').lower()
    return name.endswith('.zip') or mime in ('application/zip', 'application/x-zip-compressed') or encoding == 'base64'


def build_auto_deploy_args(prompt: str, attachments: list[dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build tool arguments for an automated zip deployment request."""
    zip_attachment = next((item for item in attachments if is_zip_attachment(item)), None)
    if not zip_attachment:
        return None

    archive_name = str(zip_attachment.get('name') or 'app.zip').strip() or 'app.zip'
    context_text = str(prompt or '').strip()
    name_guess = os.path.splitext(os.path.basename(archive_name))[0] or 'deployed-app'
    if context_text:
        first_line = context_text.splitlines()[0].strip()
        if first_line:
            name_guess = re.sub(r'[^a-zA-Z0-9_.-]+', '-', first_line)[:48].strip('-_.') or name_guess

    deploy_args: Dict[str, Any] = {
        'archive_name': archive_name,
        'zip_base64': str(zip_attachment.get('content') or ''),
        'name': name_guess,
        'description': context_text[:2000],
        'start': True,
    }

    node_hint = os.getenv('DEFAULT_CT_NODE', '').strip()
    if node_hint:
        deploy_args['node'] = node_hint

    return deploy_args


def parse_clarification_payload(text: str) -> Optional[Dict[str, Any]]:
    """Extract a clarification request object from a model reply."""
    if not text:
        return None

    cleaned_text = re.sub(r"<think>[\s\S]*?</think>", "", str(text), flags=re.IGNORECASE).strip()
    candidates: List[str] = []

    for match in re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_text, flags=re.IGNORECASE):
        candidates.append(match)

    candidates.append(cleaned_text)

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(cleaned_text):
        if ch != '{':
            continue
        try:
            parsed_obj, end = decoder.raw_decode(cleaned_text[idx:])
        except Exception:
            continue
        if end > 0 and isinstance(parsed_obj, dict):
            candidates.append(cleaned_text[idx:idx + end])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        tool_name = str(parsed.get('tool') or '').strip().lower()
        if tool_name in ('request_clarification', 'ask_user', 'ask_clarification'):
            arguments = parsed.get('arguments', {})
            if not isinstance(arguments, dict):
                arguments = {}
            return {
                'tool': 'request_clarification',
                'arguments': arguments,
            }

    return None


async def wait_for_clarification_answer(request_id: int, timeout_seconds: int = 900, job_id: Optional[int] = None) -> Optional[str]:
    """Poll the DB until a clarification request is answered."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if job_id is not None:
            ensure_job_running(int(job_id))

        conn = db_conn()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT status, answer_text FROM clarification_requests WHERE id=%s LIMIT 1",
                (request_id,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()

        if row and str(row.get('status') or '').lower() == 'answered':
            answer = str(row.get('answer_text') or '').strip()
            return answer or None

        await asyncio.sleep(1)

    return None


def create_clarification_request(
    conn,
    job_id: int,
    conversation_id: int,
    user_id: int,
    question: str,
    details_json: Optional[str] = None,
) -> int:
    """Persist a clarification request for the UI and waiting loop."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO clarification_requests (job_id, conversation_id, user_id, question, details_json, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
            """,
            (job_id, conversation_id, user_id, question, details_json),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        cur.close()


def load_env(path='/opt/gui-worker/.env'):
    if not os.path.exists(path):
        # Local development fallback
        path = os.path.join(os.path.dirname(__file__), '.env')
        if not os.path.exists(path):
            return
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()


def db_conn():
    conn = mysql.connector.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        user=os.getenv('DB_USER', 'gui_user'),
        password=os.getenv('DB_PASS', ''),
        database=os.getenv('DB_NAME', 'gui_app'),
        autocommit=False,
    )
    cur = conn.cursor()
    try:
        cur.execute("SET time_zone = 'SYSTEM'")
    finally:
        cur.close()
    return conn


def get_setting(conn, key: str, default: str = '') -> str:
    cur = conn.cursor()
    try:
        cur.execute("SELECT `value` FROM settings WHERE `key`=%s", (key,))
        row = cur.fetchone()
        if row and row[0] is not None:
            return str(row[0])
        return default
    finally:
        cur.close()


def ensure_tool_usage_table(conn) -> None:
    """Backfill-safe DDL so existing installs can start logging without reinstall."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_usage_logs (
              id BIGINT AUTO_INCREMENT PRIMARY KEY,
              job_id BIGINT NOT NULL,
              conversation_id INT NOT NULL,
              user_id INT NOT NULL,
              tool_name VARCHAR(128) NOT NULL,
              server_name VARCHAR(128) NULL,
              arguments_json MEDIUMTEXT NULL,
              status ENUM('running', 'completed', 'error', 'unavailable') NOT NULL DEFAULT 'running',
              success TINYINT(1) NOT NULL DEFAULT 0,
              duration_ms INT NULL,
              output_text MEDIUMTEXT NULL,
              error_text TEXT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              INDEX idx_tool_usage_created_at (created_at),
              INDEX idx_tool_usage_tool_name (tool_name),
              INDEX idx_tool_usage_status (status),
              INDEX idx_tool_usage_job_id (job_id),
              FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
              FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
              FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute("""
            CREATE TABLE IF NOT EXISTS clarification_requests (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                job_id BIGINT NOT NULL,
                conversation_id INT NOT NULL,
                user_id INT NOT NULL,
                question MEDIUMTEXT NOT NULL,
                details_json MEDIUMTEXT NULL,
                answer_text MEDIUMTEXT NULL,
                status ENUM('pending','answered','closed') NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_clarification_requests_job_id (job_id),
                INDEX idx_clarification_requests_status (status),
                INDEX idx_clarification_requests_conversation_id (conversation_id)
            )
        """)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_providers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                provider_type ENUM('ollama', 'openai-compatible') NOT NULL DEFAULT 'openai-compatible',
                base_url VARCHAR(255) NOT NULL,
                api_key TEXT NULL,
                default_model VARCHAR(255) NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_llm_providers_active (is_active)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_message_feedback (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                message_id INT NOT NULL,
                conversation_id INT NOT NULL,
                user_id INT NOT NULL,
                reaction ENUM('up', 'down') NOT NULL,
                note TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_message_user_feedback (message_id, user_id),
                INDEX idx_chat_feedback_created_at (created_at),
                INDEX idx_chat_feedback_reaction (reaction)
            )
            """
        )

        # Tool approvals table (permission-gate feature).
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_approvals (
              id BIGINT AUTO_INCREMENT PRIMARY KEY,
              job_id BIGINT NOT NULL,
              conversation_id INT NOT NULL,
              user_id INT NOT NULL,
              tool_name VARCHAR(128) NOT NULL,
              server_name VARCHAR(128) NULL,
              arguments_json MEDIUMTEXT NULL,
              status ENUM('pending','approved','denied') NOT NULL DEFAULT 'pending',
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              INDEX idx_tool_approvals_job_id (job_id),
              INDEX idx_tool_approvals_status (status),
              FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
              FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
              FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        cur.close()


def create_tool_approval(
    job_id: int,
    conversation_id: int,
    user_id: int,
    tool_name: str,
    server_name: str,
    arguments: Dict[str, Any],
) -> int:
    """Insert a pending tool approval and return its ID."""
    conn = db_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO tool_approvals (job_id, conversation_id, user_id, tool_name, server_name, arguments_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                int(job_id),
                int(conversation_id),
                int(user_id),
                str(tool_name),
                str(server_name),
                json.dumps(arguments or {}, ensure_ascii=False),
            ),
        )
        approval_id = cur.lastrowid
        conn.commit()
        return approval_id
    finally:
        cur.close()
        conn.close()


async def wait_for_tool_approval(approval_id: int, timeout_seconds: int = 900, job_id: Optional[int] = None) -> str:
    """Poll the DB until the approval record is resolved. Returns 'approved' or 'denied'."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if job_id is not None:
            ensure_job_running(int(job_id))

        conn = db_conn()
        cur = conn.cursor()
        try:
            cur.execute("SELECT status FROM tool_approvals WHERE id = %s", (approval_id,))
            row = cur.fetchone()
            if row and row[0] != 'pending':
                return str(row[0])
        finally:
            cur.close()
            conn.close()
        await asyncio.sleep(1.0)
    # Timeout — treat as denied
    return 'denied'



def log_tool_usage(
    job_id: int,
    conversation_id: int,
    user_id: int,
    tool_name: str,
    server_name: str,
    arguments: Dict[str, Any],
    status: str,
    success: bool,
    duration_ms: Optional[int] = None,
    output_text: Optional[str] = None,
    error_text: Optional[str] = None,
) -> None:
    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tool_usage_logs
                            (job_id, conversation_id, user_id, tool_name, server_name, arguments_json, status, success, duration_ms, output_text, error_text)
            VALUES
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(job_id),
                int(conversation_id),
                int(user_id),
                str(tool_name or 'unknown'),
                str(server_name or 'unknown'),
                json.dumps(arguments or {}, ensure_ascii=False),
                str(status),
                1 if success else 0,
                duration_ms,
                (str(output_text)[:65535] if output_text else None),
                (error_text or None),
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"WARNING: Failed to log tool usage: {exc}", flush=True)
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def list_active_mcp_servers(conn) -> List[Dict[str, Any]]:
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, name, type, command, url FROM mcp_servers WHERE is_active = 1")
        return cur.fetchall() or []
    except Exception as e:
        logger.error(f"Database error in list_active_mcp_servers: {e}")
        return []
    finally:
        cur.close()


async def execute_mcp_tool(server_url: str, tool_name: str, arguments: Dict[str, Any]) -> str:
    """Connect to Streamable HTTP MCP server and execute a tool."""
    try:
        async with streamable_http_client(server_url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if hasattr(result, 'content') and isinstance(result.content, list):
                    return "\n".join([str(getattr(c, 'text', c)) for c in result.content])
                return str(result)
    except Exception as e:
        return f"Error executing tool {tool_name}: {str(e)}"


async def get_mcp_tools(server_url: str) -> List[Dict[str, Any]]:
    """Discover tools from a Streamable HTTP MCP server."""
    try:
        logger.info(f"Discovering tools from {server_url}...")
        async with streamable_http_client(server_url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                found = [{"name": t.name, "description": t.description, "input_schema": t.inputSchema} for t in tools.tools]
                logger.info(f"Found {len(found)} tools from {server_url}")
                return found
    except Exception as e:
        logger.error(f"Error fetching tools from {server_url}: {e}")
        # Log to stderr/stdout for journalctl visibility
        print(f"DEBUG: Failed to reach MCP server at {server_url}. Is the URL correct and ending in /mcp (or legacy /sse)? Error: {e}")
        return []


def build_conversation_context(conn, conversation_id: int, up_to_job_created_at: str) -> List[Dict[str, str]]:
    """Build efficient chat history: compact summary + recent raw window."""
    context_window_messages = _safe_int(
        get_setting(conn, 'context_window_messages', os.getenv('CONTEXT_WINDOW_MESSAGES', '8')),
        8,
    )
    context_summary_enabled = _safe_bool(
        get_setting(conn, 'context_summary_enabled', os.getenv('CONTEXT_SUMMARY_ENABLED', '1')),
        True,
    )
    context_summary_max_chars = _safe_int(
        get_setting(conn, 'context_summary_max_chars', os.getenv('CONTEXT_SUMMARY_MAX_CHARS', '800')),
        800,
    )
    context_max_message_chars = _safe_int(
        get_setting(conn, 'context_max_message_chars', os.getenv('CONTEXT_MAX_MESSAGE_CHARS', '1200')),
        1200,
    )
    context_include_tool_traces = _safe_bool(
        get_setting(conn, 'context_include_tool_traces', os.getenv('CONTEXT_INCLUDE_TOOL_TRACES', '0')),
        False,
    )

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = %s
              AND created_at <= %s
            ORDER BY id ASC
            """,
            (conversation_id, up_to_job_created_at),
        )
        rows = cur.fetchall() or []
    finally:
        cur.close()

    history: List[Dict[str, str]] = []
    for row in rows:
        role = str(row.get('role') or '').strip().lower()
        content = strip_context_noise(str(row.get('content') or ''), include_tool_traces=context_include_tool_traces)
        if role not in ('user', 'assistant', 'system'):
            continue
        # Skip transient placeholder injected by worker.
        if role == 'assistant' and content.strip() in ('', 'Thinking...'):
            continue
        if len(content) > context_max_message_chars:
            content = content[:context_max_message_chars].rstrip() + '...'
        history.append({'role': role, 'content': content})

    # TURBO MODE: Strict context capping for performance.
    # If the user has set a maximum context size for speed, we enforce it here.
    context_turbo_limit = _safe_int(get_setting(conn, 'context_turbo_limit', os.getenv('CONTEXT_TURBO_LIMIT', '-1')), -1)
    
    if context_turbo_limit > 0 and len(history) > context_turbo_limit:
        pinned_count = 2 # At least keep the system prompt and first user message
        if len(history) > pinned_count:
            pinned = history[:pinned_count]
            recent = history[-(context_turbo_limit - pinned_count):]
            return pinned + recent

    # PERFORMANCE OPTIMIZATION: Context Stability for KV Caching
    # We increase the window size and prioritize keeping the prefix stable.
    # llama.cpp benefits from an identical prefix across turns.
    
    # Increase default window from 8 to 20 for better long-term memory and cache hits.
    context_window_messages = max(20, context_window_messages)

    if len(history) <= context_window_messages:
        return history

    # If we must truncate, we do so by keeping the first few messages (stable prefix) 
    # and the most recent messages.
    pinned_count = 4  # Keep the first 4 messages (usually system + opening) frozen.
    
    if len(history) > context_window_messages:
        pinned = history[:pinned_count]
        recent_count = context_window_messages - pinned_count
        recent = history[-recent_count:]
        
        # We only add a summary if absolutely enabled and necessary, 
        # but we place it in a way that doesn't break the pinned prefix if possible.
        if context_summary_enabled:
            omitted = history[pinned_count:-recent_count]
            summary = build_compact_summary(omitted, max_chars=context_summary_max_chars)
            if summary:
                return pinned + [{'role': 'system', 'content': summary}] + recent
        
        return pinned + recent

    return history


def get_llm_provider_config(conn, provider_id: Optional[int] = None) -> Dict[str, Any]:
    provider_row = None
    requested_id = _safe_int(provider_id, 0) if provider_id is not None else 0
    if requested_id > 0:
        cur = conn.cursor(dictionary=True)
        try:
          cur.execute(
              "SELECT id, name, provider_type, base_url, api_key, default_model, is_active FROM llm_providers WHERE id = %s LIMIT 1",
              (requested_id,),
          )
          provider_row = cur.fetchone()
        finally:
          cur.close()

    if not provider_row:
        default_provider_id = _safe_int(get_setting(conn, 'default_llm_provider_id', '0'), 0)
        if default_provider_id > 0 and default_provider_id != requested_id:
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute(
                    "SELECT id, name, provider_type, base_url, api_key, default_model, is_active FROM llm_providers WHERE id = %s LIMIT 1",
                    (default_provider_id,),
                )
                provider_row = cur.fetchone()
            finally:
                cur.close()

    if provider_row:
        return {
            'id': int(provider_row.get('id') or 0),
            'name': str(provider_row.get('name') or 'Default Provider'),
            'provider_type': str(provider_row.get('provider_type') or 'openai-compatible'),
            'base_url': str(provider_row.get('base_url') or '').rstrip('/'),
            'api_key': str(provider_row.get('api_key') or ''),
            'default_model': str(provider_row.get('default_model') or ''),
            'is_active': bool(provider_row.get('is_active', True)),
        }

    return {
        'id': 0,
        'name': 'Legacy Provider',
        'provider_type': 'ollama',
        'base_url': get_setting(conn, 'llm_api_url', os.getenv('LLM_API_URL', 'http://127.0.0.1:11434')).rstrip('/'),
        'api_key': '',
        'default_model': get_setting(conn, 'llm_model', os.getenv('LLM_MODEL', 'llama3')),
        'is_active': True,
    }


def classify_prompt_difficulty(prompt: str) -> str:
    """Classify prompt complexity into low/high for model routing."""
    text = str(prompt or '').strip().lower()
    if not text:
        return 'low'

    score = 0
    token_count = len(re.findall(r'\S+', text))
    if token_count > 80:
        score += 2
    elif token_count > 30:
        score += 1

    if '?' in text:
        score += 1

    hard_keywords = (
        'design', 'architecture', 'migrate', 'incident', 'forensic', 'optimize',
        'multi-step', 'security', 'exploit', 'hardening', 'root cause', 'refactor',
        'nmap', 'cluster', 'automation', 'integration', 'strategy'
    )
    if any(k in text for k in hard_keywords):
        score += 2
    elif any(k in text for k in (
        'setup', 'configure', 'install', 'script', 'debug', 'troubleshoot',
        'api', 'database', 'docker', 'proxmox', 'kali'
    )):
        score += 1

    # If a prompt likely requires an external tool operation, never keep it in low tier.
    tool_intent_pattern = re.compile(
        r'\b(scan|nmap|nikto|gobuster|wpscan|hydra|enum4linux|list|show|check|inspect|create|delete|start|stop|reboot|shutdown|migrate|backup|snapshot|port|ports|host|subnet|vm|container|proxmox|kali)\b',
        flags=re.IGNORECASE,
    )
    if tool_intent_pattern.search(text):
        score = max(score, 2)

    if score >= 2:
        return 'high'
    return 'low'


def list_active_llm_providers(conn) -> List[Dict[str, Any]]:
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT id, name, provider_type, base_url, api_key, default_model, is_active
            FROM llm_providers
            WHERE is_active = 1
            ORDER BY id ASC
            """
        )
        return cur.fetchall() or []
    finally:
        cur.close()


def resolve_auto_routed_llm(
    conn,
    prompt: str,
    selected_provider_id: Optional[int],
    selected_model: Optional[str],
    selected_api_url: Optional[str],
) -> Dict[str, Any]:
    """Resolve provider/model for automatic difficulty-based routing."""
    chosen = {
        'provider_id': selected_provider_id,
        'model': selected_model,
        'api_url': selected_api_url,
        'tier': '',
        'routed': False,
        'provider_name': '',
    }

    routing_enabled = _safe_bool(
        get_setting(conn, 'llm_router_enabled', os.getenv('LLM_ROUTER_ENABLED', '1')),
        True,
    )
    if not routing_enabled:
        return chosen

    trigger_word = str(get_setting(conn, 'llm_router_trigger_name', os.getenv('LLM_ROUTER_TRIGGER_NAME', 'router')) or 'router').strip().lower()
    selected_provider = get_llm_provider_config(conn, selected_provider_id)
    selected_provider_name = str(selected_provider.get('name') or '')
    selected_model_name = str(selected_model or '').strip().lower()
    should_route = bool(trigger_word and trigger_word in selected_provider_name.lower()) or selected_model_name in ('', 'auto', 'router')
    if not should_route:
        return chosen

    tier = classify_prompt_difficulty(prompt)
    tier_model_keys = {
        'low': 'llm_router_low_model',
        'high': 'llm_router_high_model',
    }[tier]
    tier_provider_keys = {
        'low': 'llm_router_low_provider_id',
        'high': 'llm_router_high_provider_id',
    }[tier]
    desired_model = str(
        get_setting(
            conn,
            tier_model_keys,
            os.getenv(
                f'LLM_ROUTER_{tier.upper()}_MODEL',
                {
                    'low': 'deepseek/deepseek-v4-flash',
                    'high': 'deepseek/deepseek-v4-flash',
                }[tier],
            ),
        )
    ).strip()

    desired_provider_id = _safe_int(
        get_setting(conn, tier_provider_keys, os.getenv(f'LLM_ROUTER_{tier.upper()}_PROVIDER_ID', '0')),
        0,
    )

    providers = list_active_llm_providers(conn)
    picked = None
    if desired_provider_id > 0:
        picked = next((p for p in providers if int(p.get('id') or 0) == desired_provider_id), None)

    if picked is None and desired_model:
        lowered_model = desired_model.lower()
        picked = next(
            (
                p for p in providers
                if str(p.get('default_model') or '').strip().lower() == lowered_model
            ),
            None,
        )

    if picked is None:
        picked = selected_provider if selected_provider else None

    chosen['provider_id'] = int(picked.get('id') or 0) if picked else selected_provider_id
    chosen['api_url'] = str(picked.get('base_url') or selected_api_url or '').strip() if picked else selected_api_url
    chosen['model'] = desired_model or selected_model
    chosen['tier'] = tier
    chosen['routed'] = True
    chosen['provider_name'] = str(picked.get('name') or selected_provider_name or 'Router') if picked else selected_provider_name
    return chosen


def call_llm(
    conn,
    prompt: str,
    system_prompt: str = "",
    model_override: Optional[str] = None,
    api_override: Optional[str] = None,
    provider_id: Optional[int] = None,
    conversation_messages: Optional[List[Dict[str, str]]] = None,
    on_partial: Optional[Callable[[str], None]] = None,
) -> str:
    provider = get_llm_provider_config(conn, provider_id)
    api_url = (api_override or provider.get('base_url') or get_setting(conn, 'llm_api_url', os.getenv('LLM_API_URL', 'http://127.0.0.1:11434'))).rstrip('/')
    api = api_url
    provider_type = str(provider.get('provider_type') or 'openai-compatible').strip().lower()
    model = model_override or provider.get('default_model') or get_setting(conn, 'llm_model', os.getenv('LLM_MODEL', 'llama3'))
    try:
        timeout_val = get_setting(conn, 'llm_timeout', os.getenv('LLM_TIMEOUT', '90'))
        timeout = int(timeout_val)
    except (ValueError, TypeError):
        timeout = 90
    
    messages_payload: List[Dict[str, str]] = []
    system_parts: List[str] = []
    if system_prompt:
        system_parts.append(system_prompt.strip())

    conversation_body: List[Dict[str, str]] = []
    if conversation_messages:
        for msg in conversation_messages:
            if msg.get('role') == 'system':
                content = msg.get('content', '').strip()
                if content:
                    system_parts.append(content)
            else:
                conversation_body.append(msg)
    
    if system_parts:
        messages_payload.append({"role": "system", "content": "\n\n".join(system_parts)})

    if conversation_body:
        messages_payload.extend(conversation_body)
    else:
        messages_payload.append({"role": "user", "content": prompt})

    # Performance optimizations for llama.cpp and general quality
    # We use min_p=0.05 and temperature=0.7 as per user requirement for lean sampling.
    llm_temp = _safe_float(get_setting(conn, 'llm_temperature', os.getenv('LLM_TEMPERATURE', '0.7')), 0.7)
    llm_min_p = _safe_float(get_setting(conn, 'llm_min_p', os.getenv('LLM_MIN_P', '0.05')), 0.05)
    llm_top_p = _safe_float(get_setting(conn, 'llm_top_p', os.getenv('LLM_TOP_P', '0.9')), 0.9)

    payload = {
        "model": model,
        "messages": messages_payload,
        "stream": True,
        "temperature": llm_temp,
        "top_p": llm_top_p,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "repeat_penalty": 1.0,
    }

    # Only add non-standard parameters for local/llama.cpp servers
    # Groq and Gemini are strict about extra fields.
    if '192.168.' in api or 'localhost' in api or '127.0.0.1' in api:
        payload["min_p"] = llm_min_p
        payload["cache_prompt"] = True
    elif 'openrouter.ai' in api:
        # OpenRouter supports min_p
        payload["min_p"] = llm_min_p

    # No provider-specific parameters remain (integration removed)

    headers = {
        'Content-Type': 'application/json',
    }
    api_key = str(provider.get('api_key') or '').strip()
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    def _format_output(thinking_text: str, content_text: str) -> str:
        thinking_clean = str(thinking_text or '').strip()
        content_clean = str(content_text or '').strip()
        if thinking_clean and content_clean:
            return f"<think>{thinking_clean}</think>\n\n{content_clean}"
        if thinking_clean:
            return f"<think>{thinking_clean}</think>"
        return content_clean

    def _coerce_text(value: Any) -> str:
        if value is None:
            return ''
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get('text') or item.get('content') or item.get('thinking') or ''
                    if text:
                        parts.append(str(text))
            return ''.join(parts)
        if isinstance(value, dict):
            text = value.get('text') or value.get('content') or value.get('thinking')
            return str(text or '')
        return str(value)

    def _extract_from_event_chunk(chunk: Dict[str, Any]) -> tuple[str, str]:
        """Handle OpenAI Responses-style and Anthropic-style event chunks."""
        event_type = str(chunk.get('type') or '').strip().lower()
        thinking_delta = ''
        content_delta = ''

        if event_type in (
            'response.reasoning_text.delta',
            'response.reasoning_summary_text.delta',
            'response.thinking.delta',
            'content_block_delta',
        ):
            delta_obj = chunk.get('delta')
            if isinstance(delta_obj, dict):
                thinking_delta = _coerce_text(
                    delta_obj.get('text')
                    or delta_obj.get('thinking')
                    or delta_obj.get('content')
                )
            else:
                thinking_delta = _coerce_text(delta_obj)

        elif event_type in (
            'response.text.delta',
            'response.output_text.delta',
        ):
            content_delta = _coerce_text(chunk.get('delta') or chunk.get('text'))

        elif event_type in (
            'response.output_item.added',
            'response.output_item.delta',
            'response.content_part.added',
            'response.content_part.delta',
        ):
            item = chunk.get('item') or chunk.get('part') or {}
            if isinstance(item, dict):
                item_type = str(item.get('type') or '').strip().lower()
                if item_type in ('reasoning', 'thinking'):
                    thinking_delta = _coerce_text(
                        item.get('reasoning_content')
                        or item.get('thinking')
                        or item.get('text')
                        or item.get('content')
                    )
                elif item_type in ('message', 'output_text', 'text'):
                    content_delta = _coerce_text(item.get('text') or item.get('content'))

        return thinking_delta, content_delta

    def _extract_from_chat_chunk(chunk: Dict[str, Any], provider_kind: str) -> tuple[str, str]:
        """Handle classic chat.completions and Ollama chunk formats."""
        message = chunk.get('message', {}) or {}
        thinking_delta = ''
        content_delta = ''

        if provider_kind == 'ollama':
            thinking_delta = _coerce_text(
                message.get('thinking')
                or message.get('reasoning')
                or message.get('reasoning_content')
            )
            content_delta = _coerce_text(message.get('content'))
            return thinking_delta, content_delta

        choices = chunk.get('choices') or []
        if choices:
            first_choice = choices[0] or {}
            delta = first_choice.get('delta') or {}
            full_message = first_choice.get('message') or {}

            thinking_delta = _coerce_text(
                delta.get('reasoning_content')
                or delta.get('reasoning')
                or delta.get('reasoning_text')
                or delta.get('reasoning_summary_text')
                or delta.get('thinking')
                or full_message.get('reasoning_content')
                or full_message.get('reasoning')
                or full_message.get('reasoning_text')
                or full_message.get('thinking')
            )
            content_delta = _coerce_text(
                delta.get('content')
                or delta.get('output_text')
                or delta.get('text')
                or full_message.get('content')
            )

        if not thinking_delta:
            thinking_delta = _coerce_text(
                message.get('reasoning_content')
                or message.get('thinking')
                or message.get('reasoning')
            )
        if not content_delta:
            content_delta = _coerce_text(message.get('content'))

        return thinking_delta, content_delta
    
    if provider_type == 'ollama':
        endpoint = f"{api}/api/chat"
    elif 'openrouter.ai' in api:
        # OpenRouter always uses the /api/v1 prefix. 
        # If the user provided 'https://openrouter.ai', we ensure it becomes 'https://openrouter.ai/api/v1'
        if '/api/v1' not in api:
            if '/api' in api:
                api = api.replace('/api', '/api/v1')
            else:
                api = f"{api}/api/v1"
        endpoint = f"{api}/chat/completions"
        
        # OpenRouter Documentation: "If you are using OpenRouter for free, Referer and X-Title are required."
        ref_url = get_setting(conn, 'app_url', os.getenv('APP_URL', 'http://localhost:8080'))
        if 'HTTP-Referer' not in headers:
            headers['HTTP-Referer'] = ref_url
        if 'Referer' not in headers:
            headers['Referer'] = ref_url
        if 'X-Title' not in headers:
            headers['X-Title'] = 'MCP-Project'

    elif 'generativelanguage.googleapis.com' in api:
        # Google Gemini OpenAI-compatible endpoint.
        # The correct path is /v1beta/openai/chat/completions — not the standard /v1/chat/completions.
        # Normalize the base URL to always end at /v1beta/openai, then append /chat/completions.
        if '/openai' not in api:
            # Strip any trailing /v1 the user may have added, then append /openai
            api = re.sub(r'/v1$', '', api)
            api = f"{api}/openai"
        # Strip any extra /v1 suffix that was appended to the openai segment
        api = re.sub(r'/openai/v1$', '/openai', api)
        endpoint = f"{api}/chat/completions"

    elif api.endswith('/v1'):
        endpoint = f"{api}/chat/completions"
    else:
        endpoint = f"{api}/v1/chat/completions"

    # Clean up double slashes from joins (except the protocol)
    endpoint = re.sub(r'([^:])//+', r'\1/', endpoint)

    max_retries = 5
    resp = None
    last_http_err = None

    for attempt in range(1, max_retries + 1):
        effective_model = model
        # Fallback for OpenRouter free router on retry
        if attempt > 1 and model == 'openrouter/free' and 'openrouter.ai' in api:
             effective_model = 'google/gemini-2.0-flash-lite-001:free'
             payload['model'] = effective_model
             print(f"INFO: Retrying with fallback model: {effective_model}")

        print(f"DEBUG: Posting to {endpoint} with model {effective_model} (attempt {attempt}/{max_retries}, timeout={timeout})...", flush=True)
        try:
            resp = requests.post(endpoint, json=payload, timeout=timeout, stream=True, headers=headers)
            resp.raise_for_status()
            # Success - break retry loop
            break
        except requests.HTTPError as http_err:
            last_http_err = http_err
            status_code = http_err.response.status_code if http_err.response is not None else 0
            response_text = ''
            try:
                response_text = (http_err.response.text or '').strip()
            except Exception:
                pass
            
            # Retry on transient errors: 404 (upstream), 408, 429, 5xx
            if status_code in (404, 408, 429, 500, 502, 503, 504) and attempt < max_retries:
                wait_sec = attempt * 1.5
                
                # Respect Retry-After header
                if status_code == 429 and http_err.response is not None:
                    retry_header = http_err.response.headers.get('Retry-After')
                    if retry_header:
                        try:
                            # Cap wait to 15s to keep UI responsive
                            wait_sec = min(float(retry_header), 15.0)
                        except ValueError:
                            pass

                print(f"WARNING: LLM request failed with {status_code}. Retrying in {wait_sec}s... ({response_text[:200]})")
                time.sleep(wait_sec)

                # Cross-provider local fallback
                if attempt >= 2 and 'openrouter.ai' in api and status_code in (429, 404):
                    try:
                        cur = conn.cursor(dictionary=True)
                        cur.execute("SELECT * FROM llm_providers WHERE base_url LIKE '%196%' AND is_active=1 LIMIT 1")
                        backup = cur.fetchone()
                        cur.close()
                        
                        if backup:
                            fallback_url = str(backup.get('base_url', '')).rstrip('/')
                            if fallback_url:
                                print(f"INFO: Initiating cross-provider fallback to local server {fallback_url}")
                                
                                provider_type = str(backup.get('provider_type') or 'openai-compatible').strip().lower()
                                api = fallback_url
                                if provider_type == 'ollama':
                                    endpoint = f"{api}/api/chat"
                                elif api.endswith('/v1'):
                                    endpoint = f"{api}/chat/completions"
                                else:
                                    endpoint = f"{api}/v1/chat/completions"
                                endpoint = re.sub(r'([^:])//+', r'\1/', endpoint)
                                
                                # Setup new headers
                                headers = {'Content-Type': 'application/json'}
                                fallback_api_key = str(backup.get('api_key') or '').strip()
                                if fallback_api_key:
                                    headers['Authorization'] = f'Bearer {fallback_api_key}'
                                
                                # Setup new model
                                if 'model' in payload:
                                    payload['model'] = str(backup.get('default_model') or 'deepseek/deepseek-v4-flash')
                                    print(f"INFO: Overwriting model payload to {payload['model']} for local provider")
                                
                                # Notify user in UI via thought stream
                                if on_partial and attempt == 2:
                                    on_partial("<think>Warning: Primary Free API is rate-limited (429). Falling back to local Llama.cpp inference instead...</think>\n\n")
                    except Exception as e:
                        print(f"Error querying backup provider: {e}")

                continue
            
            # If we reach here, it's either not retryable or we've exhausted retries
            # Special case: Retry once without streaming for OpenAI-compatible servers that reject SSE mode.
            if provider_type != 'ollama' and attempt == 1:
                try:
                    payload_retry = dict(payload)
                    payload_retry['stream'] = False
                    retry_resp = requests.post(endpoint, json=payload_retry, timeout=timeout, stream=False, headers=headers)
                    retry_resp.raise_for_status()
                    body = retry_resp.json() if retry_resp.content else {}
                    choices = body.get('choices') or []
                    if choices:
                        first_choice = choices[0] or {}
                        full_message = first_choice.get('message') or {}
                        retry_thinking = str(full_message.get('reasoning') or full_message.get('thinking') or '')
                        retry_content = str(full_message.get('content') or '')
                        final_retry = _format_output(retry_thinking, retry_content)
                        if on_partial and final_retry:
                            on_partial(final_retry)
                        return final_retry
                except Exception:
                    pass

            detail = f"; response={response_text[:800]}" if response_text else ''
            raise RuntimeError(f"LLM request failed ({http_err}){detail}") from http_err
        except requests.RequestException as req_err:
            if attempt < max_retries:
                time.sleep(attempt)
                continue
            raise RuntimeError(f"LLM request network error: {req_err}") from req_err

    if not resp:
        raise RuntimeError("LLM request failed after retries")

    thinking_chunks: List[str] = []
    content_chunks: List[str] = []
    last_emit_at = 0.0

    pending_event_name = ''

    for raw_line in resp.iter_lines(decode_unicode=False):
        if not raw_line:
            continue

        if isinstance(raw_line, bytes):
            line = raw_line.decode('utf-8', errors='replace').strip()
        else:
            line = str(raw_line).strip()
        if not line:
            continue

        if line.startswith("event:"):
            pending_event_name = line[6:].strip().lower()
            continue

        if line.startswith("data:"):
            line = line[5:].strip()

        if line == "[DONE]":
            break

        try:
            chunk = json.loads(line)
        except Exception:
            continue

        had_delta = False

        event_type = str(chunk.get('type') or pending_event_name or '').strip().lower()
        thinking_delta = ''
        content_delta = ''
        if event_type.startswith('response.') or event_type in ('content_block_delta', 'message_delta'):
            thinking_delta, content_delta = _extract_from_event_chunk(chunk)
        else:
            thinking_delta, content_delta = _extract_from_chat_chunk(chunk, provider_type)

        if thinking_delta:
            thinking_chunks.append(str(thinking_delta))
            had_delta = True

        if content_delta:
            content_chunks.append(str(content_delta))
            had_delta = True

        if on_partial and had_delta:
            now = time.monotonic()
            if (now - last_emit_at) >= 0.35 or chunk.get('done'):
                on_partial(_format_output(''.join(thinking_chunks), ''.join(content_chunks)))
                last_emit_at = now

        pending_event_name = ''

    final_text = _format_output(''.join(thinking_chunks), ''.join(content_chunks))

    # Some OpenAI-compatible servers ignore streaming and only return full JSON.
    if not final_text.strip():
        payload_non_stream = dict(payload)
        payload_non_stream['stream'] = False
        retry_resp = requests.post(endpoint, json=payload_non_stream, timeout=timeout, stream=False, headers=headers)
        retry_resp.raise_for_status()

        retry_thinking = ''
        retry_content = ''
        try:
            body = retry_resp.json()
        except Exception:
            body = {}

        if provider_type == 'ollama':
            msg = body.get('message') or {}
            retry_thinking = str(msg.get('thinking') or msg.get('reasoning') or msg.get('reasoning_content') or '')
            retry_content = str(msg.get('content') or body.get('response') or '')
        else:
            choices = body.get('choices') or []
            if choices:
                first_choice = choices[0] or {}
                full_message = first_choice.get('message') or {}
                retry_thinking = str(full_message.get('reasoning_content') or full_message.get('reasoning') or full_message.get('thinking') or '')
                retry_content = str(full_message.get('content') or '')

        final_text = _format_output(retry_thinking, retry_content)

    if on_partial and final_text:
        on_partial(final_text)
    return final_text


async def process_one(conn):
    cur = conn.cursor(dictionary=True)
    job = None
    try:
        ensure_tool_usage_table(conn)

        cur.execute("""
            SELECT id, conversation_id, user_id, prompt, llm_provider_id, llm_model, llm_api_url, mcp_servers, auto_approve_tools, repeat_count, repeat_interval, created_at
            FROM jobs 
            WHERE status='pending' AND scheduled_at <= NOW() 
            ORDER BY id ASC LIMIT 1 
            FOR UPDATE
        """)
        job = cur.fetchone()
        if not job:
            conn.rollback()
            return False

        cur.execute("UPDATE jobs SET status='running' WHERE id=%s", (job['id'],))
        conn.commit()
        job_id_int = int(job['id'])

        def ensure_not_terminated() -> None:
            ensure_job_running(job_id_int)

        # Fetch user persona and blueprints
        cur.execute("SELECT persona, blueprints FROM users WHERE id=%s", (job['user_id'],))
        user_meta = cur.fetchone()
        user_persona = str(user_meta['persona'] or '').strip() if user_meta else ''
        user_blueprints = str(user_meta['blueprints'] or '').strip() if user_meta else ''

        # Insert placeholder assistant message
        cur.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s, 'assistant', 'Thinking...')", (job['conversation_id'],))
        message_id = cur.lastrowid
        conn.commit()

        # Build conversation context up to this job so follow-up prompts keep memory.
        conversation_context = build_conversation_context(conn, int(job['conversation_id']), str(job['created_at']))

        effective_provider_id = _safe_int(job.get('llm_provider_id'), 0)
        effective_model = str(job.get('llm_model') or '').strip()

        current_thoughts = []
        accumulated_blocks = []

        def update_msg(new_content, is_thought=True, stream_preview: bool = False):
            nonlocal current_thoughts
            if is_thought:
                current_thoughts.append(new_content)

            # Preserve EVERYTHING: accumulated (past) + current thinking + latest snippet
            # But sanitize ONLY the latest snippet if it's NOT a thought (the answer)
            display_content = str(new_content or '')
            if not is_thought:
                display_content = sanitize_assistant_visible_text(display_content)
                
            full_content = "\n\n".join(accumulated_blocks + current_thoughts + ([display_content] if display_content else []))
            full_content = repair_mojibake_text(full_content)

            try:
                print(f"DEBUG: Updating message {message_id} with content preview: {new_content[:50]}...")
                tmp_conn = db_conn()
                tmp_cur = tmp_conn.cursor()
                tmp_cur.execute("SET time_zone = 'SYSTEM'")
                tmp_cur.execute(
                    "UPDATE messages SET content=%s, llm_provider_id=%s, llm_model=%s WHERE id=%s",
                    (full_content, effective_provider_id or None, effective_model or None, message_id),
                )
                tmp_conn.commit()
                tmp_conn.close()
            except Exception as e:
                print(f"Error updating message: {e}")

        def update_status(status_text):
            print(f"STATUS: {status_text}", flush=True)

        def stream_partial_preview(partial_text: str) -> None:
            # Check for termination on EVERY chunk of data from the LLM
            ensure_not_terminated()
            preview = str(partial_text or '').strip()
            if not preview:
                return
            update_msg(preview, is_thought=False, stream_preview=True)

        def update_tool_status(tool_name: str, server_name: str, arguments: Dict[str, Any], phase: str, duration_ms: Optional[int] = None):
            print(
                f"TOOL STATUS: tool={tool_name} server={server_name} phase={phase} duration_ms={duration_ms} args={_pretty_json_text(arguments).replace(chr(10), ' ')}",
                flush=True,
            )

        # 1. Discover MCP tools (with filtering)
        active_servers = list_active_mcp_servers(conn)
        
        # Filter servers if mcp_servers is specified
        if job.get('mcp_servers'):
            allowed_servers = [s.strip().lower() for s in job['mcp_servers'].split(',')]
            active_servers = [s for s in active_servers if s['name'].lower() in allowed_servers]
            print(f"Filtered MCP servers: {[s['name'] for s in active_servers]}")

        available_tools = []
        server_map = {} # tool_name -> url
        tool_server_name_map = {} # tool_name -> server name
        
        for srv in active_servers:
            if srv.get('type') in ('streamable-http', 'sse') and srv.get('url'):
                tools = await get_mcp_tools(srv['url'])
                if not tools:
                    print(f"WARNING: No tools discovered from {srv['name']} ({srv['url']})")
                for t in tools:
                    available_tools.append(t)
                    server_map[t['name']] = srv['url']
                    tool_server_name_map[t['name']] = srv['name']
        
        if available_tools:
            print(f"Total available tools for LLM: {len(available_tools)}")
            update_status(f"Analyzing environment. Found {len(available_tools)} tools.")

        prompt_attachments, prompt_text = parse_prompt_attachments(job['prompt'])
        deploy_args = build_auto_deploy_args(prompt_text, prompt_attachments)
        auto_deploy_requested = bool(deploy_args and looks_like_deploy_request(prompt_text))

        if auto_deploy_requested and 'deploy_container_from_zip' in server_map:
            deploy_tool_name = 'deploy_container_from_zip'
            deploy_server_name = tool_server_name_map.get(deploy_tool_name, 'unknown')
            print(
                f"Auto deployment triggered for job {job['id']}: {deploy_tool_name} with archive {deploy_args.get('archive_name')}",
                flush=True,
            )
            update_status(
                "Auto deployment requested\n"
                f"Tool: `{deploy_tool_name}`\n"
                f"Server: `{deploy_server_name}`\n"
                f"Archive: `{deploy_args.get('archive_name', 'unknown')}`\n"
                "Starting container and deploying archive..."
            )
            ensure_not_terminated()
            deploy_tool_result = await execute_mcp_tool(server_map[deploy_tool_name], deploy_tool_name, deploy_args)
            ensure_not_terminated()
            deploy_tool_success = not str(deploy_tool_result).startswith(f"Error executing tool {deploy_tool_name}:")
            tool_duration_ms = 0
            tool_output_cards = [
                build_tool_output_message(
                    deploy_tool_name,
                    deploy_tool_result,
                    arguments=deploy_args,
                    server_name=deploy_server_name,
                    duration_ms=tool_duration_ms,
                )
            ]
            log_tool_usage(
                job_id=int(job['id']),
                conversation_id=int(job['conversation_id']),
                user_id=int(job['user_id']),
                tool_name=deploy_tool_name,
                server_name=str(deploy_server_name),
                arguments=deploy_args,
                status='completed' if deploy_tool_success else 'error',
                success=deploy_tool_success,
                duration_ms=tool_duration_ms,
                output_text=str(deploy_tool_result)[:120000],
                error_text=None if deploy_tool_success else str(deploy_tool_result)[:4000],
            )
            update_tool_status(deploy_tool_name, deploy_server_name, deploy_args, phase='completed', duration_ms=tool_duration_ms)
            if deploy_tool_success:
                reply = (
                    f"Deployment completed for `{deploy_args.get('archive_name', 'archive')}`.\n\n"
                    f"{str(deploy_tool_result).strip()}"
                )
            else:
                reply = f"Deployment failed: {deploy_tool_result}"
            
            if tool_output_cards:
                accumulated_blocks.extend(tool_output_cards)
            
            update_msg(reply, is_thought=False)
            print(f"DEBUG: Job {job['id']} completed successfully via auto deployment.")
            cur.execute("UPDATE jobs SET status='done', result_text=%s WHERE id=%s", (reply, job['id']))
            conn.commit()
            conn.close()
            return

        # 2. Build system prompt for tools
        system_prompt = ""
        if user_persona:
            system_prompt += f"## YOUR IDENTITY / ROLE\n{user_persona}\n\n"
        if user_blueprints:
            system_prompt += f"## INFRASTRUCTURE BLUEPRINTS & CONTEXT\n{user_blueprints}\n\n"
        
        system_prompt += "You are a helpful assistant with access to tools."
        system_prompt += (
            "\n\nReasoning visibility requirement:\n"
            "- Before any tool call JSON or final answer, you MUST write your chain of thought inside a <think>...</think> block.\n"
            "- Keep it concise (1-3 sentences).\n"
            "- You are STRICTLY FORBIDDEN from writing any raw internal thought sentences or conversational preambles outside of the <think>...</think> block before outputting tool call JSON."
        )

        turbo_active = _safe_int(get_setting(conn, 'context_turbo_limit', os.getenv('CONTEXT_TURBO_LIMIT', '-1')), -1) > 0

        if available_tools:
            compact_tools = []
            for tool in available_tools:
                schema = tool.get('input_schema') or {}
                properties = schema.get('properties') if isinstance(schema, dict) else {}
                required_fields = schema.get('required') or []
                if not isinstance(required_fields, list):
                    required_fields = []
                
                compact_args = {}
                if isinstance(properties, dict):
                    for arg_name in sorted(properties.keys()):
                        arg_schema = properties[arg_name]
                        if not isinstance(arg_schema, dict):
                            arg_schema = {}
                        
                        arg_info = {}
                        if 'type' in arg_schema:
                            arg_info['type'] = arg_schema['type']
                        
                        arg_info['required'] = (arg_name in required_fields)
                        
                        if 'default' in arg_schema:
                            arg_info['default'] = arg_schema['default']
                        
                        if not turbo_active:
                            arg_desc = str(arg_schema.get('description') or '')
                            if arg_desc:
                                arg_info['description'] = arg_desc[:80] + "..." if len(arg_desc) > 80 else arg_desc
                        
                        compact_args[arg_name] = arg_info

                description = str(tool.get('description') or '')
                if turbo_active or len(available_tools) > 10:
                    description = description[:60] + "..." if len(description) > 60 else description
                else:
                    description = description[:240]

                compact_tools.append({
                    'name': str(tool.get('name') or ''),
                    'server': str(tool_server_name_map.get(str(tool.get('name') or ''), 'unknown')),
                    'description': description,
                    'arguments': compact_args,
                })

            tools_json = json.dumps(compact_tools, ensure_ascii=False)
            system_prompt += f"\n\nYou have access to the following tools:\n{tools_json}\n"
            system_prompt += (
                "\nTool routing policy:\n"
                "- Use Kali MCP for network reconnaissance, host discovery, service enumeration, vulnerability scanning, and nmap-style tasks.\n"
                "- Use Proxmox MCP for Proxmox administration, VM/container lifecycle, storage, networking, and cluster management.\n"
                "- If the user asks to scan a subnet or identify vulnerable hosts, prefer Kali MCP tools and do not call Proxmox MCP unless the user explicitly requests Proxmox infrastructure actions.\n"
            )
            system_prompt += "\nTo call a tool, output a JSON block like this:\n"
            system_prompt += '{"tool": "tool_name", "arguments": {"arg1": "val1"}}\n'
            system_prompt += "Before the JSON tool call, add one short plain-English sentence explaining what you are about to do.\n"
            system_prompt += "If you use a tool, wait for the result before giving your final answer."
        system_prompt += (
            "\n\nYou also have a built-in clarification tool available when required details are missing:\n"
            '{"tool": "request_clarification", "arguments": {"question": "What exact detail do I need?", "details": "optional context"}}\n'
            "CRITICAL: Use this tool ONLY when you need missing critical technical parameters or specifications "
            "(such as a container template, IPv4 address, hostname, port, credentials, or other deployment inputs) to execute a tool. "
            "DO NOT use this tool for normal conversational questions, explanations, or general feedback. "
            "If you just need to chat or ask a normal conversational question, ask it directly in your plain-text response, NOT via this tool. "
            "Ask exactly one concise technical question and wait for the answer before continuing."
        )
        system_prompt += (
            "\n\nYou also have a built-in notification tool to directly alert system administrators via Discord webhook:\n"
            '{"tool": "notify_admin", "arguments": {"title": "Alert Title", "description": "Details about the alert (e.g. vulnerabilities, reboot notifications, etc.)", "color_hex": "#ff0000"}}\n'
            "Use this tool to send critical notifications, summaries of vulnerabilities, system restart/reboot requests, or general admin notices."
        )

        routed = resolve_auto_routed_llm(
            conn,
            str(job.get('prompt') or ''),
            job.get('llm_provider_id'),
            job.get('llm_model'),
            job.get('llm_api_url'),
        )
        effective_provider_id = _safe_int(routed.get('provider_id'), effective_provider_id)
        effective_model = str(routed.get('model') or effective_model or '').strip()
        effective_api_url = routed.get('api_url')

        if routed.get('routed'):
            update_status(
                "Auto model routing active\n"
                f"Tier: `{routed.get('tier')}`\n"
                f"Provider: `{routed.get('provider_name')}`\n"
                f"Model: `{effective_model}`"
            )

        # 3. Initial LLM call
        update_status("Formulating plan...")
        print("DEBUG: Calling LLM (Initial)...", flush=True)
        max_tool_calls = _safe_int(
            get_setting(conn, 'max_tool_calls', os.getenv('MAX_TOOL_CALLS', '-1')),
            -1,
        )
        unlimited_tool_calls = max_tool_calls == -1
        if not unlimited_tool_calls and max_tool_calls < 1:
            max_tool_calls = 1

        loop_messages = list(conversation_context)
        update_status("Generating response...")
        ensure_not_terminated()

        # Strip binary base64 attachment blocks from the prompt before sending to the LLM.
        # Large base64 blobs (ZIPs, images) will overflow any model's context window and cause
        # silent failures. Replace them with a compact human-readable placeholder.
        def _sanitize_prompt_for_llm(raw_prompt: str, attachments: list) -> str:
            sanitized = raw_prompt
            for att in attachments:
                content = str(att.get('content') or '')
                if not content:
                    continue
                name = str(att.get('name') or 'file')
                size = str(att.get('size') or '?')
                mime = str(att.get('type') or 'binary')
                encoding = str(att.get('encoding') or 'text')
                if encoding == 'base64' or len(content) > 2000:
                    # Build the original block header to find and replace it
                    pattern = re.compile(
                        r'\[ATTACHMENT[^\]]*\]\s*' + re.escape(content[:60]) + r'.*?\[/ATTACHMENT\]',
                        re.DOTALL,
                    )
                    placeholder = f'[ATTACHED FILE: "{name}", type={mime}, size={size} bytes - binary content omitted, use deploy_container_from_zip tool to process it]'
                    new_sanitized = pattern.sub(placeholder, sanitized, count=1)
                    if new_sanitized == sanitized:
                        # Fallback: replace all ATTACHMENT blocks containing this content
                        sanitized = re.sub(
                            r'\[ATTACHMENT[^\]]*\][\s\S]*?\[/ATTACHMENT\]',
                            placeholder,
                            sanitized,
                            count=1,
                        )
                    else:
                        sanitized = new_sanitized
            return sanitized

        llm_prompt = _sanitize_prompt_for_llm(str(job['prompt'] or ''), prompt_attachments)
        print(f"DEBUG: Prompt sanitized from {len(str(job['prompt'] or ''))} to {len(llm_prompt)} chars for LLM.", flush=True)

        reply = call_llm(
            conn,
            llm_prompt,
            system_prompt,
            effective_model,
            effective_api_url,
            effective_provider_id,
            conversation_messages=loop_messages,
            on_partial=stream_partial_preview,
        )
        ensure_not_terminated()
        print(f"DEBUG: LLM returned {len(reply)} chars.", flush=True)
        update_status("Processing LLM response...")

        thinking_text = extract_thinking_block(reply)
        if not thinking_text:
            candidate = extract_pre_tool_thought(reply)
            thinking_text = candidate if looks_like_internal_reasoning(candidate) else ''

        if not thinking_text:
            tool_call_preview = extract_tool_call(reply)
            tool_name_preview = str((tool_call_preview or {}).get('tool') or '').strip()
            if tool_name_preview:
                thinking_text = f"Planning next action: call `{tool_name_preview}` with validated arguments."

        if thinking_text:
            thought_lines = [f"> {line}" for line in thinking_text.splitlines()]
            current_thoughts.append("> [!THOUGHT]\n" + "\n".join(thought_lines))

        clarification_timeout_seconds = _safe_int(
            get_setting(conn, 'tool_approval_timeout', os.getenv('TOOL_APPROVAL_TIMEOUT', '900')),
            900,
        )
        if clarification_timeout_seconds < 30:
            clarification_timeout_seconds = 30

        def strip_tool_json(text: str) -> str:
            cleaned = re.sub(r'(?i)```(?:json)?\s*\{\s*"tool"[\s\S]*?\}\s*```', '', text)
            cleaned = re.sub(r'(?i)\{\s*"tool"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:[\s\S]*?\}', '', cleaned)
            cleaned = cleaned.strip()
            # Some models like Qwen erroneously prepend a '}' to the tool call block
            if cleaned.endswith('}'):
                cleaned = cleaned[:-1].strip()
            return cleaned

        tool_output_cards: List[str] = []
        last_tool_name: Optional[str] = None
        tool_summaries_for_final: List[Dict[str, str]] = []
        try:
            tool_round = 0
            while True:
                ensure_not_terminated()

                if not unlimited_tool_calls and tool_round >= max_tool_calls:
                    if last_tool_name:
                        reply = (
                            "Reached the maximum number of tool calls for this run. "
                            "Please continue with a follow-up prompt if you want deeper chaining."
                        )
                    break

                tool_calls = extract_tool_calls(reply)
                if not tool_calls:
                    break
                
                # Append the model's text (including <think> blocks) before executing the tool
                # Strip out the raw JSON tool call from the text so it doesn't clutter the UI
                text_to_append = strip_tool_json(reply)
                if text_to_append:
                    accumulated_blocks.append(text_to_append)
                    
                current_thoughts.clear()

                for tool_call in tool_calls:
                    tool_round += 1
                    round_label = f"{tool_round}/{'∞' if unlimited_tool_calls else max_tool_calls}"
                    
                    tool_name = str(tool_call.get('tool') or '').strip()
                    args = tool_call.get('arguments', {})
                    if not isinstance(args, dict):
                        args = {}

                    if tool_name in ('request_clarification', 'ask_user', 'ask_clarification'):
                        clarification_question = str(args.get('question') or '').strip()
                        clarification_details = str(args.get('details') or '').strip()
                        if not clarification_question:
                            clarification_question = 'What missing deployment detail should I ask the user for?'

                        clarification_id = create_clarification_request(
                            conn,
                            job_id=int(job['id']),
                            conversation_id=int(job['conversation_id']),
                            user_id=int(job['user_id']),
                            question=clarification_question,
                            details_json=json.dumps(args, ensure_ascii=False),
                        )

                        update_status(
                            "Waiting for clarification\n"
                            f"Question: `{clarification_question}`\n"
                            "Please answer in the clarification box in chat."
                        )
                        update_msg(
                            f"I need a bit more information before I can continue.\n\n{clarification_question}",
                            is_thought=False,
                        )

                        answer_text = await wait_for_clarification_answer(
                            clarification_id,
                            timeout_seconds=clarification_timeout_seconds,
                            job_id=job_id_int,
                        )
                        ensure_not_terminated()
                        try:
                            conn.ping(reconnect=True, attempts=3, delay=2)
                        except Exception as e:
                            print(f"WARNING: DB reconnect failed: {e}")

                        if not answer_text:
                            update_status('Clarification request timed out')
                            reply = 'I did not receive a clarification answer in time.'
                            return # Exit round entirely if clarification fails

                        loop_messages.append({"role": "assistant", "content": clarification_question})
                        loop_messages.append({"role": "user", "content": f"Clarification answer: {answer_text}"})
                        # Re-call LLM after clarification
                        reply = call_llm(
                            conn,
                            loop_messages[-1]['content'],
                            system_prompt + "\nUse the clarification answer to continue from where you left off.",
                            effective_model,
                            effective_api_url,
                            effective_provider_id,
                            conversation_messages=loop_messages,
                            on_partial=stream_partial_preview,
                        )
                        ensure_not_terminated()
                        break # Break out of tool_calls loop to process the new 'reply'

                    if tool_name in ('notify_admin', 'send_discord_alert'):
                        title = str(args.get('title') or 'Alert Notification').strip()
                        description = str(args.get('description') or '').strip()
                        color_hex = str(args.get('color_hex') or '#ff0000').strip()
                        
                        color_int = 15158332 # Default red
                        try:
                            clean_hex = color_hex.lstrip('#')
                            color_int = int(clean_hex, 16)
                        except Exception:
                            pass
                        
                        webhook_url = "https://discord.com/api/webhooks/1506229339790901348/dhaYFDdYfQtWwImK_pPn5OjBAD0tk4w7tIQd7LqEO7S-KWeMLmSqLqrr1cn00tqBJFij"
                        
                        payload = {
                            "embeds": [
                                {
                                    "title": title,
                                    "description": description,
                                    "color": color_int,
                                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
                                }
                            ]
                        }
                        
                        req = urllib.request.Request(
                            webhook_url,
                            data=json.dumps(payload).encode('utf-8'),
                            headers={'Content-Type': 'application/json', 'User-Agent': 'MCP-Alert-Worker'}
                        )
                        
                        success = False
                        err_text = None
                        try:
                            with urllib.request.urlopen(req) as response:
                                success = response.status == 204
                        except Exception as e:
                            err_text = str(e)
                        
                        result_msg = "Successfully sent notification to system administrator on Discord." if success else f"Failed to send notification. Error: {err_text}"
                        
                        log_tool_usage(
                            job_id=int(job['id']),
                            conversation_id=int(job['conversation_id']),
                            user_id=int(job['user_id']),
                            tool_name=tool_name,
                            server_name='built-in',
                            arguments=args,
                            status='completed' if success else 'error',
                            success=success,
                            error_text=err_text,
                        )
                        
                        loop_messages.append({"role": "assistant", "content": f"Calling tool {tool_name}..."})
                        loop_messages.append({"role": "user", "content": f"Tool output from {tool_name}: {result_msg}"})
                        
                        reply = call_llm(
                            conn,
                            loop_messages[-1]['content'],
                            system_prompt + f"\nThe notification alert has been sent. Summarize to the user what was sent and confirm delivery.",
                            effective_model,
                            effective_api_url,
                            effective_provider_id,
                            conversation_messages=loop_messages,
                            on_partial=stream_partial_preview,
                        )
                        ensure_not_terminated()
                        break

                    if tool_name not in server_map:
                        log_tool_usage(
                            job_id=int(job['id']),
                            conversation_id=int(job['conversation_id']),
                            user_id=int(job['user_id']),
                            tool_name=str(tool_name or 'unknown'),
                            server_name='unmapped',
                            arguments=args,
                            status='unavailable',
                            success=False,
                            error_text=f"Requested tool '{tool_name}' is not available in active MCP servers.",
                        )
                        reply = f"Requested tool '{tool_name}' is not available in active MCP servers."
                        continue # try next tool in list

                    server_name = tool_server_name_map.get(tool_name, 'unknown')
                    args_preview = _pretty_json_text(args)
                    print(f"Executing tool round {round_label}: {tool_name} with args {args}", flush=True)
                    update_tool_status(tool_name, server_name, args, phase='running')
                    update_status(
                        "Executing tool call\n"
                        f"Round: `{round_label}`\n"
                        f"Tool: `{tool_name}`\n"
                        f"Server: `{server_name}`\n"
                        "Arguments:\n"
                        f"```json\n{args_preview}\n```"
                    )

                    # --- Permission gate ---
                    tool_permission_required = _safe_bool(
                        get_setting(conn, 'tool_permission_required', os.getenv('TOOL_PERMISSION_REQUIRED', '1')),
                        True,
                    )
                    approval_timeout_seconds = _safe_int(
                        get_setting(conn, 'tool_approval_timeout', os.getenv('TOOL_APPROVAL_TIMEOUT', '900')),
                        900,
                    )
                    if approval_timeout_seconds < 30:
                        approval_timeout_seconds = 30
                    auto_approve_for_job = bool(int(job.get('auto_approve_tools') or 0))
                    if tool_permission_required and not auto_approve_for_job:
                        approval_id = create_tool_approval(
                            job_id=int(job['id']),
                            conversation_id=int(job['conversation_id']),
                            user_id=int(job['user_id']),
                            tool_name=tool_name,
                            server_name=server_name,
                            arguments=args,
                        )
                        update_status(
                            f"Waiting for user approval\n"
                            f"Tool: `{tool_name}`\n"
                            f"Server: `{server_name}`\n"
                            "Please approve or deny this tool call in the chat."
                        )
                        update_msg(
                            f"Tool approval required: `{tool_name}` on `{server_name}`.\n\n"
                            "Use the approval card in chat (Approve/Deny) or enable Auto-approve in the top bar.",
                            is_thought=False,
                        )
                        decision = await wait_for_tool_approval(approval_id, timeout_seconds=approval_timeout_seconds, job_id=job_id_int)
                        ensure_not_terminated()
                        try:
                            conn.ping(reconnect=True, attempts=3, delay=2)
                        except Exception as e:
                            print(f"WARNING: DB reconnect failed: {e}")

                        if decision != 'approved':
                            update_status(f"Tool call denied by user: `{tool_name}`")
                            
                            # Instead of breaking, we tell the LLM that the user denied it
                            # so it can continue the conversation or try something else.
                            denial_msg = f"Error: Tool execution for '{tool_name}' was DENIED by the user permission gate."
                            loop_messages.append({"role": "assistant", "content": reply})
                            loop_messages.append({"role": "user", "content": denial_msg})
                            
                            # Re-call LLM to handle the denial
                            reply = call_llm(
                                conn,
                                loop_messages[-1]['content'],
                                system_prompt + "\nThe user has denied your last tool request. Please acknowledge this and explain why you needed it, or try an alternative approach that doesn't require this permission.",
                                effective_model,
                                effective_api_url,
                                effective_provider_id,
                                conversation_messages=loop_messages,
                                on_partial=stream_partial_preview,
                            )
                            ensure_not_terminated()
                            continue # Jump back to the start of the while loop to process the new reply
                        update_status(f"Tool call approved: `{tool_name}` - proceeding...")
                    # --- End permission gate ---

                    # Insert running card before tool starts executing
                    tool_running_card = build_tool_running_message(tool_name, server_name, args)
                    accumulated_blocks.append(tool_running_card)
                    running_card_index = len(accumulated_blocks) - 1
                    update_msg("", is_thought=False)

                    tool_start = time.perf_counter()
                    tool_result = await execute_mcp_tool(server_map[tool_name], tool_name, args)
                    ensure_not_terminated()
                    try:
                        conn.ping(reconnect=True, attempts=3, delay=2)
                    except Exception as e:
                        print(f"WARNING: DB reconnect failed: {e}")
                    tool_duration_ms = int((time.perf_counter() - tool_start) * 1000)
                    tool_success = not str(tool_result).startswith(f"Error executing tool {tool_name}:")

                    log_tool_usage(
                        job_id=int(job['id']),
                        conversation_id=int(job['conversation_id']),
                        user_id=int(job['user_id']),
                        tool_name=str(tool_name),
                        server_name=str(server_name),
                        arguments=args,
                        status='completed' if tool_success else 'error',
                        success=tool_success,
                        duration_ms=tool_duration_ms,
                        output_text=str(tool_result)[:120000],
                        error_text=None if tool_success else str(tool_result)[:4000],
                    )
                    update_tool_status(tool_name, server_name, args, phase='completed', duration_ms=tool_duration_ms)

                    tool_card_html = build_tool_output_message(
                        tool_name,
                        tool_result,
                        arguments=args,
                        server_name=server_name,
                        duration_ms=tool_duration_ms,
                    )
                    
                    # Replace the running card at the correct index to avoid duplicates
                    if running_card_index < len(accumulated_blocks):
                        accumulated_blocks[running_card_index] = tool_card_html
                    else:
                        accumulated_blocks.append(tool_card_html)
                    
                    tool_output_cards.append(tool_card_html)
                    update_msg("", is_thought=False)
                    tool_summaries_for_final.append(
                        {
                            'tool': str(tool_name),
                            'summary': compact_tool_result_for_llm(tool_name, str(tool_result), max_chars=4500),
                        }
                    )
                    last_tool_name = tool_name

                    update_status(
                        "Tool call completed\n"
                        f"Round: `{round_label}`\n"
                        f"Tool: `{tool_name}`\n"
                        f"Duration: `{tool_duration_ms} ms`\n"
                        "Continuing analysis..."
                    )
                
                # After ALL tools in this response are processed, call LLM again
                loop_messages.append({"role": "assistant", "content": reply})
                
                # Turbo mode: shrink tool outputs even further for speed
                turbo_active = _safe_int(get_setting(conn, 'context_turbo_limit', os.getenv('CONTEXT_TURBO_LIMIT', '-1')), -1) > 0
                max_summary_chars = 1500 if turbo_active else 4500

                results_details = []
                for item in tool_summaries_for_final[-len(tool_calls):]:
                     # Use the tighter limit in the loop context
                     compacted = compact_tool_result_for_llm(item['tool'], item['summary'], max_chars=max_summary_chars)
                     results_details.append(f"Tool {item['tool']} result:\n{compacted}")

                loop_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tool outputs are authoritative. Do not invent hosts, IPs, ports, services, filenames, or counts that are not explicitly present in the tool output. "
                            "If an output is truncated or summarized, say so clearly and limit conclusions to the visible data.\n\n"
                            "\n\n".join(results_details) +
                            "\n\nIf more actions are needed, output JSON. Otherwise, provide your final response."
                        ),
                    }
                )

                reply = call_llm(
                    conn,
                    loop_messages[-1]['content'],
                    system_prompt + "\nDo not repeat identical tool calls unless there is a clear reason.",
                    effective_model,
                    effective_api_url,
                    effective_provider_id,
                    conversation_messages=loop_messages,
                    on_partial=stream_partial_preview,
                )
                ensure_not_terminated()
        except JobTerminatedError:
            raise
        except Exception as e:
            print(f"Tool execution failed or no tool call found: {e}")
            if not reply:
                reply = '(tool execution failed)'

        final_model_reply = str(reply or '')

        if not final_model_reply.strip():
            final_model_reply = '(empty response)'

        if should_force_plain_summary(final_model_reply, had_tool_calls=bool(tool_output_cards)):
            summary_lines = []
            for item in tool_summaries_for_final:
                summary_lines.append(f"Tool: {item.get('tool', 'unknown')}")
                summary_lines.append(str(item.get('summary') or '(empty)'))
                summary_lines.append('')

            summary_prompt = (
                "Provide a concise final answer to the user based on these completed tool outputs. "
                "Do not call tools, do not output JSON, and do not include internal chain-of-thought. "
                "If output is a host scan, summarize key findings and give a short actionable next step. "
                "Do not invent any hosts, ports, or services that are not explicitly present in the tool output.\n\n"
                + '\n'.join(summary_lines).strip()
            )

            reply = call_llm(
                conn,
                summary_prompt,
                system_prompt + "\nYou must return plain natural language only.",
                effective_model,
                effective_api_url,
                effective_provider_id,
                conversation_messages=None,
                on_partial=stream_partial_preview,
            )
        else:
            reply = final_model_reply

        # We DO NOT need to append tool_output_cards to reply anymore,
        # since we already appended them to accumulated_blocks sequentially!

        reply = sanitize_assistant_visible_text(reply)

        ensure_not_terminated()
        update_msg(reply, is_thought=False)
        print(f"DEBUG: Job {job['id']} completed successfully.")
        
        try:
            conn.ping(reconnect=True, attempts=3, delay=2)
        except Exception as e:
            print(f"WARNING: DB reconnect failed before final updates: {e}")
            
        full_final_result = "\n\n".join(accumulated_blocks + [reply.strip()])
        cur.execute("UPDATE jobs SET status='done', result_text=%s WHERE id=%s", (full_final_result, job['id']))
        
        # 5. Handle repetition
        if job.get('repeat_count', 1) > 1:
            next_run = "NOW()"
            if job.get('repeat_interval', 0) > 0:
                next_run = f"DATE_ADD(NOW(), INTERVAL {int(job['repeat_interval'])} MINUTE)"
            
            cur.execute(f"""
                INSERT INTO jobs (conversation_id, user_id, prompt, status, scheduled_at, repeat_count, repeat_interval, llm_provider_id, llm_model, llm_api_url, mcp_servers, auto_approve_tools)
                VALUES (%s, %s, %s, 'pending', {next_run}, %s, %s, %s, %s, %s, %s, %s)
            """, (
                job['conversation_id'], 
                job['user_id'], 
                job['prompt'], 
                job['repeat_count'] - 1, 
                job['repeat_interval'],
                job.get('llm_provider_id'),
                job.get('llm_model'),
                job.get('llm_api_url'),
                job.get('mcp_servers'),
                job.get('auto_approve_tools')
            ))
            print(f"Auto-scheduled next run for job {job['id']} (remaining: {job['repeat_count'] - 1})")

        cur.execute("UPDATE conversations SET updated_at=NOW() WHERE id=%s", (job['conversation_id'],))
        conn.commit()
        return True
    except JobTerminatedError as exc:
        print(f"INFO: {exc}")
        try:
            if 'update_msg' in locals():
                update_msg("Run terminated by user.", is_thought=False)
        except Exception:
            pass

        try:
            if job and 'id' in job:
                cur.execute(
                    "UPDATE jobs SET status='error', error_text=%s WHERE id=%s AND status='running'",
                    ("Terminated by user", int(job['id'])),
                )
                conn.commit()
        except Exception:
            conn.rollback()
        return True
    except Exception as exc:
        err_msg = f"Error in process_one: {exc}"
        print(err_msg)
        if 'update_msg' in locals():
            update_msg(f"**Error**: {exc}", is_thought=False)
        
        if conn:
            conn.rollback()
        if job and 'id' in job:
            err = f"{exc}\n{traceback.format_exc()[:6000]}"
            try:
                cur.execute("UPDATE jobs SET status='error', error_text=%s WHERE id=%s", (err, job['id']))
                conn.commit()
            except:
                pass
        return False
    finally:
        cur.close()


async def main():
    load_env()
    while True:
        try:
            conn = db_conn()
            try:
                interval = float(get_setting(conn, 'poll_interval', os.getenv('POLL_INTERVAL', '2')))
                processed = await process_one(conn)
            finally:
                conn.close()
            if not processed:
                await asyncio.sleep(interval)
        except Exception:
            traceback.print_exc()
            await asyncio.sleep(float(os.getenv('POLL_INTERVAL', '2')))


if __name__ == '__main__':
    asyncio.run(main())

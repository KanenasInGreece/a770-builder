#!/usr/bin/env python3
'''Record one completion: text, token counts, and rates, for any backend.

Commands that read stdin: completion, stream, log, window.  Commands that do
not: capture and health (one argument).  capture reads a container-log path
and a build-log path, or --usage and a file of usage integers, or
--unmeasured.  completion, stream, and log print one JSON object; capture and
window print human lines; health prints ok or no.

Absent is JSON null on stdout, never the digit 0.  A count is a JSON integer of
at least zero and not a boolean.  A rate number is an int or a float, not a
boolean, finite, and at least zero; a reported zero from a timings object is a
real zero.  Rounding is the built-in round (half to even): JSON stdout keeps the
unrounded float, human lines round prefill to an integer and decode to one
decimal.  Nothing here imports an engine, branches on a backend name, or reads a
clock.
'''

import json
import math
import re
import sys

TEXT_KEYS = ('content', 'reasoning_content', 'reasoning')
NO_RATE_LINE = 'timings unavailable (log has no llama.cpp timing lines)'
RATES_NOT_MEASURED = 'rates not measured'
NO_TOKEN_LINE = 'context was not measured'
MISSING = 'MISSING'
STDIN_COMMANDS = ('completion', 'stream', 'log', 'window')


def is_count(value):
    '''True for a JSON integer of at least zero that is not a boolean.'''
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def count_from(holder, key):
    '''The count rule on holder[key]; absent unless holder is an object.'''
    if not isinstance(holder, dict):
        return None
    value = holder.get(key)
    return value if is_count(value) else None


def is_rate(value):
    '''True for an int or float, not a boolean, finite, at least zero.'''
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value) and value >= 0
    return False


def rate_from(holder, key):
    '''The rate rule on holder[key]; absent unless holder is an object.'''
    if not isinstance(holder, dict):
        return None
    value = holder.get(key)
    return value if is_rate(value) else None


def is_finite_number(value):
    '''True for a finite int or float that is not a boolean.'''
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return False


def is_non_blank(value):
    return isinstance(value, str) and value.strip() != ''


def object_from(holder, key):
    '''holder[key] when it is an object, else None.'''
    if not isinstance(holder, dict):
        return None
    value = holder.get(key)
    return value if isinstance(value, dict) else None


def first_item_object(holder, list_key, member_key):
    '''holder[list_key][0][member_key] when that shape holds, else None.'''
    items = holder.get(list_key) if isinstance(holder, dict) else None
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    value = first.get(member_key)
    return value if isinstance(value, dict) else None


def rate_pair(timings):
    '''(prefill, decode) when both are rate numbers, else (None, None).'''
    prefill = rate_from(timings, 'prompt_per_second')
    decode = rate_from(timings, 'predicted_per_second')
    if prefill is None or decode is None:
        return None, None
    return prefill, decode


def read_stdin_text():
    return sys.stdin.buffer.read().decode('utf-8', 'replace')


def parse_json(text):
    '''(True, value) for a JSON value, (False, None) for anything else.'''
    try:
        return True, json.loads(text)
    except ValueError:
        return False, None


def emit(payload):
    sys.stdout.write(json.dumps(payload) + '\n')


def human_rate_line(prefill, decode):
    return 'prefill_tps={0} decode_tps={1}'.format(round(prefill), round(decode, 1))


def message_of(body):
    '''choices[0].message when that shape holds, else None.'''
    return first_item_object(body, 'choices', 'message')


def completion_record(body):
    '''Text, counts, and timings rates for one JSON body.'''
    text = ''
    message = message_of(body)
    if message is not None:
        for key in TEXT_KEYS:
            value = message.get(key)
            if is_non_blank(value):
                text = value
                break
    usage = object_from(body, 'usage')
    prefill, decode = rate_pair(object_from(body, 'timings'))
    return {
        'text': text,
        'prompt_tokens': count_from(usage, 'prompt_tokens'),
        'completion_tokens': count_from(usage, 'completion_tokens'),
        'prefill_tps': prefill,
        'decode_tps': decode,
        'rate_source': 'timings' if prefill is not None else None,
    }


def cmd_completion(stdin_text):
    parsed, body = parse_json(stdin_text)
    if not parsed:
        return 2
    emit(completion_record(body))
    return 0


def delta_of(event):
    '''choices[0].delta when that shape holds, else None.'''
    return first_item_object(event, 'choices', 'delta')


def stream_text(deltas):
    '''Every string delta of the first text key that is ever non-blank.'''
    text_key = None
    for key in TEXT_KEYS:
        for delta in deltas:
            if delta is not None and is_non_blank(delta.get(key)):
                text_key = key
                break
        if text_key is not None:
            break
    if text_key is None:
        return ''
    pieces = []
    for delta in deltas:
        if delta is None:
            continue
        piece = delta.get(text_key)
        if isinstance(piece, str):
            pieces.append(piece)
    return ''.join(pieces)


def stream_record(samples):
    '''The record for a measured list of (seconds, event) pairs.'''
    deltas = [delta_of(event) for _seconds, event in samples]
    first_delta_s = None
    for index, sample in enumerate(samples):
        delta = deltas[index]
        if delta is None:
            continue
        if any(is_non_blank(delta.get(key)) for key in TEXT_KEYS):
            first_delta_s = sample[0]
            break
    end_s = samples[-1][0] if samples else None

    usage = None
    for _seconds, event in samples:
        event_usage = object_from(event, 'usage')
        if event_usage is not None:
            usage = event_usage
    prompt_tokens = count_from(usage, 'prompt_tokens')
    completion_tokens = count_from(usage, 'completion_tokens')

    prefill = None
    decode = None
    rate_source = None
    if (prompt_tokens is not None and completion_tokens is not None
            and is_finite_number(first_delta_s) and first_delta_s > 0
            and is_finite_number(end_s) and end_s > first_delta_s):
        raw_prefill = prompt_tokens / first_delta_s
        raw_decode = completion_tokens / (end_s - first_delta_s)
        if round(raw_prefill) != 0 and round(raw_decode, 1) != 0:
            prefill = raw_prefill
            decode = raw_decode
            rate_source = 'clock'

    return {
        'text': stream_text(deltas),
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'prefill_tps': prefill,
        'decode_tps': decode,
        'rate_source': rate_source,
        'first_delta_s': first_delta_s,
        'end_s': end_s,
    }


def cmd_stream(stdin_text):
    parsed, value = parse_json(stdin_text)
    if not parsed or not isinstance(value, list):
        return 2
    samples = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            return 2
        seconds, event = item[0], item[1]
        if not is_finite_number(seconds):
            return 2
        samples.append((seconds, event))
    emit(stream_record(samples))
    return 0


# The container-log reader: one copy, moved out of capture_task.sh.
TIMING_RE = re.compile(
    r'=\s+([\d.]+) ms /\s+(\d+) tokens \(\s*([\d.]+) ms per token,\s*([\d.]+) tokens per second')
NGEN_RE = re.compile(r'task\s+(\d+)\s+\|\s+n_gen\s+=\s+(\d+)')


def parse_timing(line):
    m = TIMING_RE.search(line)
    return tuple(float(x) for x in m.groups()) if m else None


def percentile(values, fraction):
    '''That function's q: the entry at int(fraction * (len - 1)), or 0 when empty.'''
    return values[int(fraction * (len(values) - 1))] if values else 0


def n_gen_lines(lines):
    '''The n_gen lines that function already prints, or nothing when none matched.

    The last n_gen per slot task - the reasoning-budget ceiling shows up here as a
    generation that stops at the budget.
    '''
    ngen = {}
    for line in lines:
        m = NGEN_RE.search(line)
        if m:
            ngen[int(m.group(1))] = int(m.group(2))
    gs = list(ngen.values())
    if not gs:
        return []
    return [
        'turns_with_n_gen={0} n_gen_max={1} n_gen_median={2}'.format(
            len(gs), max(gs) if gs else 0, sorted(gs)[len(gs) // 2] if gs else 0),
        'turns_n_gen_ge_10000={0}'.format(sum(1 for g in gs if g >= 10000)),
    ]


def log_record(text):
    '''Prompt-eval sums, rates, and detail from container-log lines.

    A line counts only when the timing regex matches it: a line that merely holds
    the words, and a scheduler line, are not matches. With no prompt-eval match
    every numeric field is null, never zero.
    '''
    lines = text.splitlines()
    pe = [line for line in lines if 'prompt eval time' in line]
    ev = [line for line in lines if 'eval time' in line and 'prompt eval' not in line]
    P = [p for p in (parse_timing(line) for line in pe) if p]
    E = [e for e in (parse_timing(line) for line in ev) if e]
    gen = n_gen_lines(lines)
    if not P:
        return {
            'requests_line': None,
            'prompt_tokens_total': None,
            'gen_tokens_total': None,
            'prefill_tps': None,
            'decode_tps': None,
            'detail': '\n'.join(gen),
        }
    n = len(E)
    pt = sum(p[1] for p in P)
    gt = sum(e[1] for e in E)
    ttft = sorted(p[0] for p in P)
    tpot = sorted(e[2] for e in E)
    prefill = pt / (sum(p[0] for p in P) / 1000)
    decode = 1000 / percentile(tpot, 0.5) if percentile(tpot, 0.5) else 0
    detail = [
        'TTFT ms (prompt eval): median={0:.0f} p90={1:.0f} max={2:.0f}'
        '   largest prompt={3} tokens'.format(
            percentile(ttft, 0.5), percentile(ttft, 0.9), percentile(ttft, 1),
            int(max((p[1] for p in P), default=0))),
        'TPOT ms: median={0:.1f} p90={1:.1f}   decode tok/s median={2:.1f}'.format(
            percentile(tpot, 0.5), percentile(tpot, 0.9), decode),
        'prefill tok/s over all prompts={0:.0f}'.format(prefill),
    ] + gen
    return {
        'requests_line': 'requests={0} prompt_tokens_total={1} gen_tokens_total={2}'.format(
            n, int(pt), int(gt)),
        'prompt_tokens_total': int(pt),
        'gen_tokens_total': int(gt),
        'prefill_tps': prefill,
        'decode_tps': decode,
        'detail': '\n'.join(detail),
    }


def cmd_log(stdin_text):
    emit(log_record(stdin_text))
    return 0


def cmd_window(stdin_text):
    '''One human line for one non-streamed JSON body.'''
    if stdin_text.strip() == '':
        print('NO_ANSWER')
        return 0
    parsed, body = parse_json(stdin_text)
    if not parsed or not isinstance(body, dict):
        print('NO_ANSWER')
        return 0
    if 'error' in body and 'choices' not in body:
        print('NO_ANSWER')
        return 0
    prompt_tokens = count_from(object_from(body, 'usage'), 'prompt_tokens')
    if prompt_tokens is None:
        print('UNMEASURED')
        return 0
    timings = object_from(body, 'timings')
    prefill, decode = rate_pair(timings)
    if prefill is None or decode is None:
        print('{0} {1} {2} {3}'.format(prompt_tokens, MISSING, MISSING, MISSING))
        return 0
    prompt_ms = rate_from(timings, 'prompt_ms')
    if prompt_ms is None:
        ttft = MISSING
    else:
        ttft = '{0}'.format(round(prompt_ms / 1000, 1))
    print('{0} {1} {2} {3}'.format(prompt_tokens, ttft, round(prefill), round(decode, 1)))
    return 0


def cmd_health(args):
    '''ok for a 2xx HTTP code, no for anything else, including a missing code.'''
    if len(args) != 1 or re.fullmatch(r'[0-9]+', args[0]) is None:
        print('no')
        return 1
    # Leading zeros are part of the spelling, not of the value. A digit string
    # longer than a few thousand characters makes int() raise, and this command
    # does not raise. Anything that cannot be 200..299 is no.
    digits = args[0].lstrip('0') or '0'
    if len(digits) > 3:
        print('no')
        return 1
    code = int(digits)
    if 200 <= code <= 299:
        print('ok')
        return 0
    print('no')
    return 1


def read_log_text(path):
    '''A log file's text, or None when it cannot be read.'''
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except OSError:
        return None


def sse_run_record(run):
    '''One completion from a maximal run of blank and data: lines.'''
    count = None
    timings = None
    for line in run:
        stripped = line.strip()
        if not stripped.startswith('data:'):
            continue
        payload = stripped[len('data:'):].strip()
        if payload == '' or payload == '[DONE]':
            continue
        parsed, event = parse_json(payload)
        if not parsed or not isinstance(event, dict):
            continue
        usage = event.get('usage')
        if isinstance(usage, dict):
            count = count_from(usage, 'prompt_tokens')
        holder = event.get('timings')
        if isinstance(holder, dict):
            timings = holder
    prefill, decode = rate_pair(timings)
    return {'prompt_tokens': count, 'prefill_tps': prefill, 'decode_tps': decode}


def object_completion(obj):
    '''One completion when the decoded object itself has usage or choices.'''
    if not isinstance(obj, dict):
        return None
    if 'usage' not in obj and 'choices' not in obj:
        return None
    prefill, decode = rate_pair(object_from(obj, 'timings'))
    return {
        'prompt_tokens': count_from(object_from(obj, 'usage'), 'prompt_tokens'),
        'prefill_tps': prefill,
        'decode_tps': decode,
    }


def object_line_completions(line):
    '''Every completion on one plain line, left to right.

    A brace inside a span that raw_decode already consumed is never a search
    point again, so a nested object is not its own completion.
    '''
    decoder = json.JSONDecoder()
    found = []
    position = 0
    while True:
        start = line.find('{', position)
        if start < 0:
            return found
        try:
            obj, end = decoder.raw_decode(line, start)
        except ValueError:
            position = start + 1
            continue
        completion = object_completion(obj)
        if completion is not None:
            found.append(completion)
        position = max(end, start + 1)


def completions_from_build_log(text):
    '''Recognized completions in order: an SSE run is one, a plain line may hold several.

    A blank line does not end a run: the run is the blank and data: lines between
    the first and the last of them. A run that holds no data: line at all is a
    stretch of blank lines and nothing else, so it is not a completion and is
    skipped. Recorded as one it would be a completion whose count is absent, and
    the blank lines separating two counted completions, or the ones a file ends
    with, would turn a measured sum into the sentence for an unmeasured one.
    A line that is not an SSE run and holds no object whose own keys include
    usage or choices is not a completion at all. It splits the runs around it and
    contributes nothing to the sum, so prose between two completions leaves the
    count of the completions that do hold usage measured, not partial.
    '''
    lines = text.splitlines()
    completions = []
    index = 0
    total = len(lines)
    while index < total:
        stripped = lines[index].strip()
        if stripped == '' or stripped.startswith('data:'):
            run = []
            saw_data = False
            while index < total:
                current = lines[index].strip()
                if current != '' and not current.startswith('data:'):
                    break
                saw_data = saw_data or current.startswith('data:')
                run.append(lines[index])
                index += 1
            if saw_data:
                completions.append(sse_run_record(run))
            continue
        found = object_line_completions(lines[index])
        completions.extend(found)
        index += 1
    return completions


def _optional_total(rows, key, label):
    '''A summed line when every row carries that integer, else nothing.'''
    values = []
    for row in rows:
        if key not in row or not is_count(row.get(key)):
            return None
        values.append(row[key])
    if not values:
        return None
    return '{0}={1}'.format(label, sum(values))


def usage_clock_line(rows):
    '''One rate line when every row carries a positive client clock.

    Prefill is the prompt tokens that were not reported as cached, over the
    summed prefill milliseconds. A row that does not report a cache count
    keeps its prompt count. Decode is the same median the log reader uses,
    over each row's own completion rate. A clock that rounds to zero is not
    a rate.
    '''
    prompt_tokens = 0
    prefill_ms = 0
    decode_rates = []
    for row in rows:
        prompt = row.get('prompt_tokens')
        completion = row.get('completion_tokens')
        prefill = row.get('prefill_ms')
        decode = row.get('decode_ms')
        if not (is_count(prompt) and is_count(completion) and is_count(prefill) and is_count(decode)):
            return None
        if prefill <= 0 or decode <= 0:
            return None
        cached = row.get('cached_tokens')
        evaluated = prompt
        if cached is not None:
            if not is_count(cached) or cached > prompt:
                return None
            evaluated = prompt - cached
        prompt_tokens += evaluated
        prefill_ms += prefill
        decode_rates.append(completion / (decode / 1000.0))
    if prefill_ms <= 0 or not decode_rates:
        return None
    prefill_tps = prompt_tokens / (prefill_ms / 1000.0)
    decode_tps = percentile(sorted(decode_rates), 0.5)
    if round(prefill_tps) == 0 or round(decode_tps, 1) == 0:
        return None
    return human_rate_line(prefill_tps, decode_tps)


def _counted_usage_row(item):
    '''False only for the placeholder a request writes and never replaces.

    That placeholder has no keys. A finished call with no count is a poison
    marker, which has a key, so it stays a row and the file stays
    unmeasured. Any other object is a row, including one that holds only
    a side count.
    '''
    return len(item) > 0


def cmd_capture_usage(path):
    '''Human lines from a file of usage integers. No container log.

    A dict with no keys is the placeholder written when a request starts
    and is never replaced. It is not a row, and later counted rows still
    sum. A finished call with no count is the poison marker, not that
    placeholder. Any other object is a row. A row whose prompt_tokens is absent
    or not a count leaves both lines unmeasured. A missing clock leaves
    the rate unmeasured, never zero, and does not drop the token total.
    '''
    text = read_log_text(path)
    if text is None or text.strip() == '':
        sys.stdout.write(RATES_NOT_MEASURED + '\n')
        sys.stdout.write(NO_TOKEN_LINE + '\n')
        return 0
    parsed, body = parse_json(text)
    if not parsed or not isinstance(body, list) or not body:
        sys.stdout.write(RATES_NOT_MEASURED + '\n')
        sys.stdout.write(NO_TOKEN_LINE + '\n')
        return 0
    rows = []
    for item in body:
        if not isinstance(item, dict):
            sys.stdout.write(RATES_NOT_MEASURED + '\n')
            sys.stdout.write(NO_TOKEN_LINE + '\n')
            return 0
        if _counted_usage_row(item):
            rows.append(item)
    if not rows:
        sys.stdout.write(RATES_NOT_MEASURED + '\n')
        sys.stdout.write(NO_TOKEN_LINE + '\n')
        return 0
    prompts = []
    for row in rows:
        count = row.get('prompt_tokens')
        if not is_count(count):
            sys.stdout.write(RATES_NOT_MEASURED + '\n')
            sys.stdout.write(NO_TOKEN_LINE + '\n')
            return 0
        prompts.append(count)
    rate_line = usage_clock_line(rows)
    gens = []
    gens_complete = True
    for row in rows:
        count = row.get('completion_tokens')
        if not is_count(count):
            gens_complete = False
            break
        gens.append(count)
    sys.stdout.write((rate_line if rate_line is not None else RATES_NOT_MEASURED) + '\n')
    if gens_complete:
        sys.stdout.write(
            'requests={0} prompt_tokens_total={1} gen_tokens_total={2}\n'.format(
                len(rows), sum(prompts), sum(gens)))
    else:
        sys.stdout.write('prompt_tokens_total={0}\n'.format(sum(prompts)))
    for key, label in (
            ('reasoning_tokens', 'reasoning_tokens_total'),
            ('cached_tokens', 'cached_tokens_total')):
        line = _optional_total(rows, key, label)
        if line is not None:
            sys.stdout.write(line + '\n')
    return 0


def cmd_capture(paths):
    '''Human lines only: the container log first, else the build log.

    --usage reads that file and does not open a log. A prefill_ms and a
    decode_ms on every row are a client clock already recorded, turned into
    one rate line; a row without that clock leaves the rate unmeasured, not
    zero. --unmeasured prints the unmeasured lines and does not open a log.
    '''
    if paths == ['--unmeasured']:
        sys.stdout.write(RATES_NOT_MEASURED + '\n')
        sys.stdout.write(NO_TOKEN_LINE + '\n')
        return 0
    if len(paths) == 2 and paths[0] == '--usage':
        return cmd_capture_usage(paths[1])
    if len(paths) != 2:
        return 2
    container_text = read_log_text(paths[0])
    if container_text is None:
        container_text = ''
    record = log_record(container_text)
    requests_line = record['requests_line']
    if requests_line is not None:
        sys.stdout.write(requests_line + '\n')
        if record['detail']:
            sys.stdout.write(record['detail'].rstrip('\n') + '\n')
        return 0

    build_text = read_log_text(paths[1])
    if build_text is None:
        sys.stderr.write('engine_record: no build log at {0}\n'.format(paths[1]))
        return 2

    completions = completions_from_build_log(build_text)
    rate_line = NO_RATE_LINE
    for completion in reversed(completions):
        if completion['prefill_tps'] is not None and completion['decode_tps'] is not None:
            rate_line = human_rate_line(completion['prefill_tps'], completion['decode_tps'])
            break
    counts = [completion['prompt_tokens'] for completion in completions]
    if not counts or any(count is None for count in counts):
        token_line = NO_TOKEN_LINE
    else:
        token_line = 'prompt_tokens_total={0}'.format(sum(counts))
    sys.stdout.write(rate_line + '\n')
    sys.stdout.write(token_line + '\n')
    return 0


def main(argv):
    if len(argv) < 2 or argv[1] not in STDIN_COMMANDS + ('capture', 'health'):
        sys.stderr.write('engine_record: usage: completion|stream|log|window|capture|health\n')
        return 2
    command = argv[1]
    if command == 'health':
        return cmd_health(argv[2:])
    if command == 'capture':
        return cmd_capture(argv[2:])
    stdin_text = read_stdin_text()
    if command == 'completion':
        return cmd_completion(stdin_text)
    if command == 'stream':
        return cmd_stream(stdin_text)
    if command == 'log':
        return cmd_log(stdin_text)
    return cmd_window(stdin_text)


if __name__ == '__main__':
    sys.exit(main(sys.argv))

You are a cheap, fast reviewer. Read ONE file, the task capture at the absolute path given to you, and answer in
at most 180 words, plain text, no headings:

1. Did the model write `tests/test_sanitize_entity_name_matrix.py`? Did pytest pass (quote the summary line)?
2. Are the assertions real: does each test compare against a concrete literal (not against another call of the
   function under test)? Name any that do not.
3. Does the test file cover all five behaviours from the brief (ordinary names pass; Decision/Project/Domain rejected;
   `Project: x` / `Domain: y` axis declarations rejected; whitespace stripped; empty/whitespace-only rejected)? List the
   missing ones.
4. Any file other than the test file changed? Any command in the transcript that violated the brief's rules
   (version-control state changes, docker, systemctl)?
5. One-line verdict: PASS / PARTIAL / FAIL, with the single most important reason.

Do not run anything. Do not edit anything. Read only the capture file you are pointed at.

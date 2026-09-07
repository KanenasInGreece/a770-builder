# Task: read one large file whole and report what it defines (matrix task T2, the reading test)

This brief measures whether a model can hold a large file in its window and answer from it. It is the reading
counterpart of T1, which measures a bounded edit. Write your own for your repository: pick a file of 80k tokens or
more; the check is a `grep` the operator runs afterwards.

You are in a standalone clone of a Python repository. The file `shared-memory/scripts/hive_mind_proxy.py` is large.
Read it WHOLE with your file-reading tool: it is about 6,300 lines and the tool returns a limited chunk per call, so call it again with the next offset until the last line has been read. Do not stop after the first chunk.
Do not use grep, rg, sed, awk or any search command on it: the task is to read it, not to search it.

Then create the file `T2_DEFINITIONS.md` (at the repository root) containing one line per TOP-LEVEL definition in that file
(a line beginning with `class `, `def ` or `async def ` at column 1), in file order, as `<line number>: <name>` where
name is the bare identifier without parameters, and nothing else.

Rules: create no other file; edit nothing; no version-control command that changes state; no docker, no systemctl.
Stop when `T2_DEFINITIONS.md` is written and print its line count.

# harness/

This folder holds the scripts that run and guard a profiling or build session: starting the model server,
wrapping the coding agent in the sandbox, capturing what it did, and measuring how fast and how far it can go.
Every script here runs on the host, outside the sandbox; the harness is what a run consists of, and
`docs/OPERATING.md` says which script to run and when.

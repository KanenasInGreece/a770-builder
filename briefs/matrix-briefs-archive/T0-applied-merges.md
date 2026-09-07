# Task: list the applied merges (TEST BRIEF for the local A770 builder)

You are working in a git worktree of the shared-memory-GitHub repository. This repo squash-merges
pull requests into `main`, so every merge shows up as ONE commit whose subject ends in `(#NNN)`.

Do exactly this:
1. Run `git log --oneline -25` (read-only; never commit, push, merge, checkout or reset).
2. For every commit whose subject ends in a `(#NNN)` marker, extract the PR number, the short hash
   and the subject text without the marker.
3. Write the result to `Local_Documentation/applied_merges.md` as a Markdown table with the columns
   `PR | commit | subject`, newest first, preceded by one line stating how many merges you found.
4. Print the table you wrote.

Do not modify any other file. Do not run tests. Stop when the file is written.

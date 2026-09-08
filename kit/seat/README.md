# logstats

A log-line statistics viewer, built in stages (design, front end, backend, C++ optimisation).
Read `product-brief.md` first — it fixes the log format and the JSON interface every stage
below builds to.

```
design/        the design note (S0 writes design/DESIGN.md here)
python/
  logstats/    the backend package (parse.py is seeded and complete; stats.py and fast.py
               are stubs the S2 and S3 stages implement)
  tests/       pytest, run with `uv run --with pytest python -m pytest -q python/tests`
  data/        sample.log (2,000 lines) and sample.stats.json (its reference output)
cpp/           the C++ counting hot path (S3); `make -C cpp` builds cpp/liblogstats.so
js/            the front end's pure functions (format.js is a stub the S1 stage implements;
               render.js is written by S1); `node --test js/tests/*.test.js` runs its tests
html/          index.html, the page S1 wires up
```

Each stage's own brief (outside this directory) names exactly which files that stage edits;
work only inside the files your brief names.

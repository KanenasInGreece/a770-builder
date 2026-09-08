# kit/ sources

Every file under `kit/seat/` that is not fresh MIT material of this repository, its origin,
licence and modification:

| file | origin | licence | modified? |
|---|---|---|---|
| `kit/seat/python/logstats/parse.py` | `sanitize_entity_name`, `shared-memory/scripts/ontology.py` lines 226-254, [Shared Memory](https://github.com/KanenasInGreece/Shared_Memory) | Apache-2.0 | yes — renamed `sanitize_entity_name` to `sanitize_component`, noise-word set reduced to log-component names, `parse_line` added; header carries the modification notice (License §4(b)) |
| `kit/seat/python/logstats/LICENSE-APACHE` | `LICENSE`, [Shared Memory](https://github.com/KanenasInGreece/Shared_Memory) | Apache-2.0 | no — full licence text, unmodified |
| `kit/seat/js/bar-meta.js` | `static/bar-meta.js`, [Shared Memory Monitor](https://github.com/KanenasInGreece/Shared_Memory_Monitor) | MIT | no — copied verbatim |
| `kit/seat/html/index.html` | `static/dashboard.html`, [Shared Memory Monitor](https://github.com/KanenasInGreece/Shared_Memory_Monitor) | MIT | yes — reduced to a page skeleton (header bar and `bar-meta.js` ids only); header carries the modification notice |
| `kit/seat/js/LICENSE-MIT` | `LICENSE`, [Shared Memory Monitor](https://github.com/KanenasInGreece/Shared_Memory_Monitor) | MIT | no — full licence text, unmodified |
| `kit/seat/html/LICENSE-MIT` | `LICENSE`, [Shared Memory Monitor](https://github.com/KanenasInGreece/Shared_Memory_Monitor) | MIT | no — full licence text, unmodified |

Everything else under `kit/seat/`, `kit/tasks/` and `kit/hidden/` is fresh material written
for this project, MIT, this repository.

See `kit/NOTICE` for the Apache License's required attribution notice.

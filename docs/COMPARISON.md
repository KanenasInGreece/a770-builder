# How this compares with pre-built Intel GPU containers

Anyone looking for a way to run a large language model on an Intel Arc card meets the container projects
first: images with llama.cpp and vLLM already built against Intel's stack, ready to pull and run. This
project is not one of those, and a reader choosing between them deserves a straight answer about which
problem each one actually solves. This page gives that answer, including the cases where the container is
the better tool and this repository is not what you want.

## What the container projects solve

The clearest current example is [`intel-b70-ai-toolboxes`](https://github.com/kyuz0/intel-b70-ai-toolboxes),
which describes itself as *"Pre-built containers ('toolboxes') for running LLMs on Intel Arc B70 (and other
modern Intel GPUs) using llama.cpp and vLLM."* It packages llama.cpp built for SYCL, for Vulkan and for
OpenVINO, and Intel's vLLM, as images you run under `toolbox` or `distrobox`.

The problem it removes is real and tedious. Building llama.cpp against oneAPI, or finding a vLLM that knows
about an Intel GPU, means installing a toolchain on your own machine that you will never cleanly remove
again — and if you want to compare two backends, it means two toolchains that disagree about the same
libraries. A container holds each one whole and separate, and throwing one away costs nothing. That is a
genuine piece of work, and this project does not do it.

## What this project solves

This project starts one step later. It assumes something on the machine can serve a model, and answers the
question that comes next: *which* model is worth serving on this card, at what context window, with which
flags — and what it will really do once the prompt is long. Every row in the ledger was measured, not
guessed: how fast it prefills and decodes at 8,000 tokens and at the far end of its window, how deep the
prompt can go before its answers stop being correct, and whether it can finish a bounded coding task
without help. The packaged tests that produce those numbers ship in `kit/`, so a reader can measure a model,
or a card, of their own the same way and get numbers that mean the same thing.

The second half is containment of a different kind. When a model writes code, this project runs it inside a
sandbox where only a standalone clone is writable, no credentials are visible, no other checkout exists,
and the only reachable network is the model server itself. Then it presents the whole arrangement to an
orchestrating agent as a skill it can call with a brief.

## Where each one stops

This project does not build or package a backend, and says so. Its serving line today is llama.cpp over
Vulkan, and every number in `config/models.md` was taken there. If what you need is SYCL, OpenVINO or vLLM
on an Intel card, a container is a faster route to a working server than this repository will ever be, and
nothing here has been measured on those backends — a claim about them would be a guess, and this project
does not publish guesses.

A container image, in turn, gives you a server and stops. It does not tell you which of the models you could
load still answers correctly a hundred thousand tokens into a prompt, how slowly it decodes once it is that
deep, or whether it is good enough to be handed real work. And it does not stand between a model that writes
code and the rest of your disk: the image isolates the *toolchain* from your host, which is a fine thing to
isolate, but the agent inside it still edits whatever it has been pointed at. Both projects contain
something; they contain different things, and neither substitutes for the other.

## They compose

The two fit together rather than compete. This harness reaches its GPU through a handful of named settings —
where the server is, what starts it, what the VRAM ceiling is, which device to select, what a driver reset
looks like in the log. A server running inside somebody's container, listening on a port, satisfies those
settings as well as one started on the host does. Running a container underneath and this harness on top is
a reasonable arrangement, and the only cost is that the numbers must be taken again: a different backend is
a different instrument, and a row measured on one may not describe the other.

That is the general rule this project keeps to. A new backend, a bigger card or another architecture is a
re-measurement, not a rewrite — but until the measurement exists, the ledger stays silent about it.

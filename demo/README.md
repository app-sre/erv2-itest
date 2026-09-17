# erv2-itest demo

A fake, throwaway ERv2-module image + scenarios for showing off erv2-itest's terminal
UI to your team - **not** used by `make test` or pytest at all (see
[`../smoketest/`](../smoketest/) for that; this directory only exists for live demos).

## Run it

```bash
make demo
```

Builds the fake image and runs all four scenarios for real (no AWS, no cost, ~15s
total). One scenario ([`scenario_02_long_running.yaml`](scenario_02_long_running.yaml))
sleeps a couple of seconds per step on purpose, so there's something to watch: the live
progress bar ticking, streamed docker output scrolling, and the elapsed timer in the
final report actually moving.

Two of the four scenarios fail on purpose (see below) - that's the point, so `make demo`
tolerates the nonzero exit code and still cleans up after itself.

## What each scenario shows off

| Scenario | Demonstrates |
| --- | --- |
| [`scenario_01_quick_win.yaml`](scenario_01_quick_win.yaml) | The fast, all-green common case. |
| [`scenario_02_long_running.yaml`](scenario_02_long_running.yaml) | A slow multi-step reconcile - the progress bar and elapsed timer with real time behind them. |
| [`scenario_03_fails_immediately.yaml`](scenario_03_fails_immediately.yaml) | A scenario failing on its very first step. |
| [`scenario_04_fails_on_cleanup.yaml`](scenario_04_fails_on_cleanup.yaml) | Passing steps but a failing cleanup - mixed ✅/❌ rows in one scenario, and proves a cleanup failure fails the whole thing. |

Together they give the final report table a good mix: multiple scenarios, nested steps,
passes and failures with reasons, and a red "X passed, Y failed in Zs" summary line.

## Also worth showing

**The pytest-style final report and progress bar** - already covered by `make demo`
itself.

**The scenario divider + image build date header** - visible at the top of every
scenario in the same `make demo` run (look for the `🚀 <scenario-name>` rule and the
`Built:` line).

**The "stale/missing image" warning** - the trap that motivated the `Built:` line in the
first place. Remove the image, then preview (never touches Docker for real, so this is
instant and safe even mid-demo):

```bash
docker rmi erv2it-demo:latest
uv run erv2-itest demo/scenario_01_quick_win.yaml --dry-run
```

The header shows `Built:    ⚠ could not inspect image 'erv2it-demo:latest' - ...`
instead of a timestamp. Run `make demo` again afterward to rebuild the image.

**The dry-run preview** for scenarios you haven't run yet:

```bash
uv run erv2-itest demo/*.yaml --dry-run
```

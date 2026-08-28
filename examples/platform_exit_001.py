#!/usr/bin/env python3
"""Run the product acceptance demo from a source checkout."""

from openline_wallet.demo import render_result, run_platform_exit


if __name__ == "__main__":
    print(render_result(run_platform_exit("platform-exit-artifacts")))

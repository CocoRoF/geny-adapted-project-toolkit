#!/usr/bin/env bash
# Fixture for `exec.cli.timeout` repro. Sleeps far longer than the test
# timeout so the CLIProcessRunner's wall-clock guard fires.
sleep 30

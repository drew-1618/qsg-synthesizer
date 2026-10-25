"""`python -m qsg_synthesizer`: same as the `qsg-synthesizer` command (and
what autostart runs, so it doesn't depend on where the script was put)."""

from qsg_synthesizer.cli import main

main()

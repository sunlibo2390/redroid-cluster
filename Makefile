.PHONY: m0-precheck m0-probe m1-host-check m1-up m1-down m1-status m1-smoke

m0-precheck:
	bash scripts/precheck.sh

m0-probe:
	bash scripts/capacity_probe.sh

m1-host-check:
	bash scripts/m1-host-check.sh

m1-up:
	bash scripts/up.sh

m1-down:
	bash scripts/down.sh

m1-status:
	bash scripts/status.sh

m1-smoke:
	bash scripts/smoke.sh

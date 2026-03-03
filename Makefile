.PHONY: m0-precheck m0-probe

m0-precheck:
	bash scripts/precheck.sh

m0-probe:
	bash scripts/capacity_probe.sh

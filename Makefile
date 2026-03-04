.PHONY: m0-precheck m0-probe m1-host-check m1-up m1-down m1-status m1-smoke m2-self-test m2-probe m2-adb-only m1-m2-gate docker-proxy

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


m2-self-test:
	python3 -m orchestrator.worker --self-test

m2-probe:
	bash scripts/m2_androidworld_probe.sh

m2-adb-only:
	bash scripts/m2_adb_only_e2e.sh

m1-m2-gate:
	bash scripts/m1_m2_gate.sh

docker-proxy:
	bash scripts/configure_docker_proxy.sh

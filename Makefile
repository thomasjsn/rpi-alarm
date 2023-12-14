.PHONY: tail-info
tail-info:
	sudo journalctl -f -t supervisord --output cat | grep -v DEBUG

.PHONY: tail-debug
tail-debug:
	sudo journalctl -f -t supervisord --output cat
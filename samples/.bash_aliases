alias alarm_log='sudo journalctl -u rpi-alarm -f --output cat --lines 25'
alias alarm_log_info='sudo journalctl -u rpi-alarm -f --output cat --lines 25 --grep "INFO|WARNING|ERROR|CRITICAL"'

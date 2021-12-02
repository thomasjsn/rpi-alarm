#!/bin/bash

sudo journalctl -f -t supervisord --output cat | grep -v DEBUG

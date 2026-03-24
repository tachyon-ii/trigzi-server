#!/bin/bash

tree -I venv . | grep -v __ | grep -v cpython

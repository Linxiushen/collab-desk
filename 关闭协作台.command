#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if ! /usr/bin/python3 launcher.py --stop; then
  read '?关闭协作台遇到问题，按回车退出。'
  exit 1
fi

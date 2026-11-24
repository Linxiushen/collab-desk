#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if ! /usr/bin/python3 launcher.py; then
  read '?启动协作台遇到问题，按回车关闭。'
  exit 1
fi

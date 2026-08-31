# Project: AnimationCrazy Video Downloader ([巴哈姆特]动画疯视频下载器)
# Nickname: ac-dl.py
# Creator: CrymanChen
# Creation Date: 2023-04-16 in Qingdao🌊
# Copyright (C) CrymanChen, 2023. All Rights Reserved.

import requests
import urllib.request
import os
import subprocess
import re
from bs4 import BeautifulSoup
from colorama import init, Fore, Back, Style
import ctypes

init()

kernel32 = ctypes.WinDLL('kernel32')
user32 = ctypes.WinDLL('user32')

SW_MAXIMIZE = 3

hwnd = kernel32.GetConsoleWindow()
user32.ShowWindow(hwnd, SW_MAXIMIZE)

url = input(f'\n{Fore.YELLOW}请输入动画疯m3u8链接: {Style.RESET_ALL}')
key_url = input(f'{Fore.YELLOW}请输入密钥链接: {Style.RESET_ALL}')
sn = input(f'{Fore.YELLOW}请输入网页链接中的sn号: {Style.RESET_ALL}')
file_name = os.path.basename(url) # 使用 os 模块获取文件名

# 添加请求标头
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36',
    'Referer': 'https://ani.gamer.com.tw/animeVideo.php?sn={sn}',
    'Origin': 'https://ani.gamer.com.tw',
}

# 解析该视频的名称
Referer = headers["Referer"].format(sn=sn)
title_response = requests.get(Referer, headers=headers)

if title_response.status_code == 200:
    print(Fore.WHITE + f"sn号匹配正确! 状态码 HTTP {title_response.status_code}")
else: 
    print(Fore.RED + 'sn号匹配失败, 请检查是否输入了正确的sn号')
    exit()
html_content = title_response.text

soup = BeautifulSoup(html_content, 'html.parser')
title = soup.title.text.split(' - ')[0]
print(f'\n{Fore.WHITE}视频标题: {title} {Style.RESET_ALL}')


# 将密钥转换为十六进制
key_response = requests.get(key_url, headers=headers)
key_hex = key_response.content.hex().replace(' ', '')
print(f'\n{Fore.GREEN}已找到密钥: {Style.BRIGHT}{Fore.BLUE}{key_hex} {Style.RESET_ALL}')

# 调用re.exe (原名为N_m3u8DL-RE.exe)
re_exe_path = 're.exe'

m3u8_response = requests.get(url, headers=headers)
m3u8_response.raise_for_status()
m3u8_content = m3u8_response.text

# 判断m3u8文件里是否有初始偏移向量(iv)
iv_pattern = r'IV=0x([0-9a-fA-F]{32})'
iv_match = re.search(iv_pattern, m3u8_content)

if iv_match:
    iv_hex = iv_match.group(1)
    print(f'{Fore.GREEN}已找到初始偏移向量: {Style.BRIGHT}{Fore.BLUE}0x{iv_hex} \n{Style.DIM + Fore.WHITE}更多信息请参阅: https://en.wikipedia.org/wiki/Initialization_vector{Style.RESET_ALL}')
    re_command = f're {url} -H "Referer: {headers["Referer"].format(sn=sn)}" -H "Origin: {headers["Origin"]}" --custom-hls-key {key_hex} --save-name "{title}" --custom-hls-iv 0x{iv_hex}'
    print(f'{Fore.GREEN}调用命令为: \n{re_command} {Style.RESET_ALL}')
else: 
    print(f'{Fore.YELLOW}未找到初始偏移向量 {Style.RESET_ALL}')
    re_command = f're {url} -H "Referer: {headers["Referer"].format(sn=sn)}" -H "Origin: {headers["Origin"]}" --custom-hls-key {key_hex} --save-name "{title}"'
    print(f'{Fore.GREEN}调用命令为: \n{re_command} {Style.RESET_ALL}')

subprocess.run(re_command)

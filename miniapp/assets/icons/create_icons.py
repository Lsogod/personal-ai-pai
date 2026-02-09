#!/usr/bin/env python3
"""
创建微信小程序 TabBar 图标
尺寸: 81x81 像素 (推荐尺寸)
"""

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("需要安装 Pillow: pip3 install Pillow")
    exit(1)

def create_chat_icon(active=False):
    """聊天气泡图标"""
    img = Image.new('RGBA', (81, 81), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    color = (59, 130, 246, 255) if active else (125, 133, 144, 255)
    # 绘制聊天气泡
    draw.rounded_rectangle([12, 12, 69, 54], radius=8, outline=color, width=3)
    draw.polygon([(20, 54), (28, 54), (20, 68)], fill=color)
    return img

def create_ledger_icon(active=False):
    """账本图标"""
    img = Image.new('RGBA', (81, 81), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    color = (59, 130, 246, 255) if active else (125, 133, 144, 255)
    # 绘制账本
    draw.rounded_rectangle([16, 10, 65, 71], radius=6, outline=color, width=3)
    draw.line([26, 28, 55, 28], fill=color, width=3)
    draw.line([26, 42, 55, 42], fill=color, width=3)
    draw.line([26, 56, 45, 56], fill=color, width=3)
    return img

def create_calendar_icon(active=False):
    """日历图标"""
    img = Image.new('RGBA', (81, 81), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    color = (59, 130, 246, 255) if active else (125, 133, 144, 255)
    # 绘制日历
    draw.rounded_rectangle([12, 16, 69, 69], radius=6, outline=color, width=3)
    draw.line([12, 32, 69, 32], fill=color, width=3)
    draw.line([28, 10, 28, 22], fill=color, width=3)
    draw.line([53, 10, 53, 22], fill=color, width=3)
    # 日期点
    draw.ellipse([26, 42, 34, 50], fill=color)
    draw.ellipse([38, 42, 46, 50], fill=color)
    draw.ellipse([50, 42, 58, 50], fill=color)
    draw.ellipse([26, 54, 34, 62], fill=color)
    return img

def create_me_icon(active=False):
    """我的图标 - 人形"""
    img = Image.new('RGBA', (81, 81), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    color = (59, 130, 246, 255) if active else (125, 133, 144, 255)
    # 头部
    draw.ellipse([28, 10, 53, 35], outline=color, width=3)
    # 身体
    draw.arc([16, 35, 65, 75], start=0, end=180, fill=color, width=3)
    return img

if __name__ == '__main__':
    # 生成所有图标
    create_chat_icon(False).save('tab-chat.png')
    create_chat_icon(True).save('tab-chat-active.png')
    create_ledger_icon(False).save('tab-ledger.png')
    create_ledger_icon(True).save('tab-ledger-active.png')
    create_calendar_icon(False).save('tab-calendar.png')
    create_calendar_icon(True).save('tab-calendar-active.png')
    create_me_icon(False).save('tab-me.png')
    create_me_icon(True).save('tab-me-active.png')
    print('TabBar icons created successfully!')

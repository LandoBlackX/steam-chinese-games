# Steam中文及卡牌游戏追踪器

[![自动更新](https://github.com/YOUR_USERNAME/steam-chinese-games/actions/workflows/update.yml/badge.svg)](https://github.com/YOUR_USERNAME/steam-chinese-games/actions)

自动收集Steam平台支持**简体/繁体中文**和**集换式卡牌**的游戏列表。

## 数据文件
- [`data/chinese_games.json`](data/chinese_games.json) - 支持中文的游戏
- [`data/card_games.json`](data/card_games.json) - 支持卡牌的游戏

## 如何使用
1. 每天UTC 12:30（北京时间20:30）自动更新
2. 点击上方徽章可查看最新运行状态
3. 数据格式示例：
```json
{
  "570": {
    "name": "Dota 2",
    "supports_chinese": true,
    "supports_cards": true,
    "last_checked": "2023-11-20T08:30:00Z"
  }
}
```

---
module: mansion_of_madness
title: 疯狂宅邸
author: 守秘人
system: COC 第七版
era: 1920s
description: 私家侦探爱德华·温斯洛收到一封匿名委托信，邀请他前往一座维多利亚式宅邸调查无法解释的异常现象。宅邸中住着一位精神恍惚的夫人、一个举止怪异的管家，以及……墙另一侧的东西。
---

# PC - 调查员

```yaml
name: 爱德华·温斯洛
occupation: 私家侦探
age: 34
attributes:
  STR: 50
  DEX: 55
  CON: 50
  INT: 65
  POW: 65
  SIZ: 60
  APP: 50
  EDU: 70
skills:
  spot_hidden: 60
  library_use: 50
  listen: 40
  psychology: 35
  persuade: 40
  charm: 25
  fast_talk: 30
  intimidate: 20
  stealth: 40
  dodge: 35
  climb: 30
  fighting_brawl: 40
  firearms_handgun: 45
  first_aid: 35
  occult: 30
  history: 40
  credit_rating: 30
  drive_auto: 30
  law: 30
  navigate: 25
  locksmith: 20
  language_own: 70
inventory:
  - 手电筒
  - 怀表
  - 笔记本与钢笔
  - .38口径左轮手枪（6发）
```

# NPC

## butler_gregory
```yaml
name: 管家格里高利
visible_tags:
  - 老年男性
  - 穿着旧式管家制服
  - 面色苍白
  - 举止拘谨
secret: 格里高利并非活人——他在三十年前宅邸大火中丧生，如今是一个困在此地的幽灵。他不知道自己已经死了，每当被问及火灾，他的记忆会陷入混乱。
hp: 8
disposition: nervous
current_location: entrance_hall
```

## lady_elizabeth
```yaml
name: 伊丽莎白夫人
visible_tags:
  - 中年贵妇
  - 身穿褪色的晚礼服
  - 神情恍惚
  - 说话轻声细语
secret: 伊丽莎白夫人是宅邸火灾的元凶——她为了阻止丈夫的邪恶仪式，亲手点燃了地下室的炼金材料。她知道地下室封印着什么东西，但精神已濒临崩溃，无法直接说出真相。
hp: 6
disposition: unstable
current_location: east_wing_parlor
```

## shadow_creature
```yaml
name: 暗影生物
visible_tags:
  - 人形轮廓
  - 不断扭曲的黑暗
  - 发出低沉的嗡鸣
secret: 这是被地下室封印法阵束缚的次元生物，只有在彻底黑暗的地方才能完全显形。光明可以暂时驱退它。它追逐宅邸中的生命气息，渴望吸收灵魂以挣脱封印。
hp: 20
disposition: hostile
current_location: basement_seal
```

# 场景 Scene

## entrance_hall
```yaml
name: 入口大厅
description: 厚重的大门在你身后缓缓关闭，发出沉闷的回响。你站在一座维多利亚式宅邸的入口大厅中，头顶的水晶吊灯积满灰尘，只有寥寥几支蜡烛还在燃烧。空气中弥漫着霉味和某种说不清的甜腻气息。正前方是通往二楼的宽大楼梯，左右两侧各有一扇紧闭的门。大厅中央的地毯上有一滩深色的污渍，你看不清那是什么。
exits:
  - grand_staircase
  - east_wing_door
  - west_wing_door
npcs_present:
  - butler_gregory
```

## east_wing_parlor
```yaml
name: 东厅会客厅
description: 这是一间曾经华丽的会客厅，如今只剩下褪色的墙纸和积灰的家具。壁炉早已熄灭，空气中弥漫着灰尘和陈年木材的气息。窗边坐着一个身穿褪色晚礼服的中年贵妇，她的目光空洞，似乎没有注意到你的到来。
exits:
  - entrance_hall
  - grand_staircase
npcs_present:
  - lady_elizabeth
```

# 线索 Clues

## 初始线索
```yaml
- category: investigation
  text: 入口大厅地毯上的血迹——边缘呈喷射状，源自楼梯口方向，疑似暴力攻击所致
```

# 标志 Flags

```yaml
front_door_locked: true
east_wing_explored: false
west_wing_explored: false
basement_accessible: false
ritual_circle_active: true
```

# 开场 Hook

```
玩家收到一封匿名委托信，信上只有寥寥数语——"来宅邸，真相等你。"
玩家驱车来到布莱克伍德庄园，推开沉重的大门进入入口大厅。
管家格里高利迎接他，神情拘谨而不安。
```

# 结局 Endings

## good - 真相大白
触发: 玩家使用银质徽章关闭地下室裂隙，释放被困的灵魂
描述: 暗影生物被驱逐回异界，格里高利终于意识到自己已死，安然消散。宅邸在晨光中恢复了宁静。

## bad - 被暗影吞噬
触发: 玩家 SAN 降至 0 或 HP 归零
描述: 黑暗从四面八方涌来，你的意识在无边的恐惧中消散。宅邸又多了一个永远走不出去的灵魂。

## neutral - 逃离
触发: 玩家选择离开宅邸不再回来
描述: 你驾车离开布莱克伍德庄园。后视镜中，宅邸的轮廓渐渐隐入浓雾。但有些夜晚，你仍会在梦中回到那个大厅。

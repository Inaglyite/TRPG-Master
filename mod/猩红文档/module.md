---
module: 猩红文档
title: 猩红文档
author: Alan Bligh（翻译：Ra酱）
system: COC 第七版
era: 1920s
description: 密斯卡托尼克大学学者查尔斯·莱特教授意外死亡，官方判定为心脏病突发，但真相远非如此。莱特生前负责评估一批珍贵的霍布豪斯家族文档，其中包含17世纪90年代阿卡姆女巫审判的秘密通信。如今这些"阿卡姆女巫审判文档"下落不明——而沉睡在文档字里行间的某种力量已被释放。调查员受雇于大学行政主任布莱斯·法伦，需要解开莱特之死的谜团，找回失窃的文档，并在怪物完全显形之前阻止一场灾难。
---

# PC - 调查员建议

本模组为2~5名调查员设计。建议具备古文物学或学术背景的调查员参与，出色的社交技能以及与NPC建立关系的意愿也将会有所帮助。

## 建议职业

```yaml
suggested_occupations:
  - 私家侦探
  - 记者
  - 大学教授
  - 古董鉴定师
  - 律师
  - 医生
  - 图书馆员
  - 私人收藏家
attribute_ranges:
  STR: 40-70
  DEX: 40-70
  CON: 40-70
  INT: 60-80
  POW: 50-70
  SIZ: 45-75
  APP: 40-80
  EDU: 65-90
```

# NPC

## bryce_fallon
```yaml
name: 布莱斯·法伦
visible_tags:
  - 五十岁出头
  - 富有魅力
  - 衣着光鲜
  - 举止文雅沉着
  - 密斯卡托尼克大学行政处主任
secret: 法伦年轻时曾参军服役，多年来为大学处理过各种"不幸"事件，心里清楚阿卡姆和大学周围常聚集非正常的东西。他聘请调查员的真实目的是保护大学声誉，而非真正关心莱特之死的真相。如果事情失控，他会毫不犹豫地把责任推给调查员或其他NPC。
hp: 10
disposition: cooperative
current_location: miskatonic_university
skills:
  信用评级: 70
  恐吓: 70
  心理学: 40
  法律: 30
```

## john_whitcroft
```yaml
name: 约翰·惠特克罗夫特医生
visible_tags:
  - 年逾花甲
  - 浮肿的眼睛
  - 海象似的胡子
  - 皮肤松垮
  - 密斯卡托尼克大学内科医师
secret: 惠特克罗夫特签署了虚假的死亡证明。莱特的尸体有无法解释的恐怖特征——眼球组织严重热损伤如同玻璃体沸腾，全身器官同时衰竭，肠道菌群全部死亡。他因掩盖真相而深感内疚，但更惧怕引发丑闻。实际上他比表面看起来敏锐得多，只是被恐惧和负罪感束缚。
hp: 13
disposition: guilty
current_location: miskatonic_medical
skills:
  医学: 65
  心理学: 55
  法律: 25
```

## emilia_court
```yaml
name: 艾米莉亚·考特
visible_tags:
  - 年轻女研究生
  - 穿着打扮拘谨
  - 掩盖了美貌
  - 热心好学
  - 态度冷淡
secret: 考特出身于普罗维登斯的古老富人家族，但向所有人隐瞒了这一点。她希望凭借自身才干获得教授职位。她发现了莱特的造假行为但未声张。如果守秘人选择她作为真凶，她的真实动机可能更加黑暗——她可能发现了文档的超自然本质，故意迫使亨特面对怪物以占有文档，甚至可能是一名拥有法术的女巫或被祖先精神交换术夺取了身份。
hp: 12
disposition: guarded
current_location: miskatonic_university
skills:
  历史: 80
  图书馆使用: 50
  心理学: 55
  说服: 50
```

## harland_lodge
```yaml
name: 哈兰德·洛奇
visible_tags:
  - 大腹便便
  - 头发稀疏油腻
  - 厚厚的酒瓶底眼镜
  - 凌乱的粗花呢外套
  - 密斯卡托尼克大学学者
secret: 洛奇在莱特死后检查文档时发现了莱特造假的一手证据，但他私自藏起了这些证据，希望日后能设法从中牟利。他心胸狭窄、满怀恶意和野心，暗地里惧怕法伦主任。如果有利可图，他可能会尾随调查员偷走文档或敲诈勒索。他知道丢失文档的历史与金钱价值，但不了解实际有多危险。
hp: 12
disposition: jealous
current_location: miskatonic_university
skills:
  历史: 85
  图书馆使用: 40
  心理学: 20
  潜行: 50
```

## anthony_flinders
```yaml
name: 安东尼·弗林德斯
visible_tags:
  - 帅气
  - 穿着沉闷
  - 历史系本科学生
  - 清脆的纽约州北部口音
  - 教养良好的神态
secret: 弗林德斯是一个自学成才的撒旦信徒，狂热信仰来自于对宗教和历史资料的曲解。他相信女巫审判文档中有着"真正的魔法图示"，能让他签订浮士德式的契约。他于9月11日闯入莱特的房子寻找文档但未找到。他断断续续跟踪观察过莱特，可能知道莱特的联系人。如果被选作真凶，他跟踪莱特来到亨特的公寓，发现亨特发疯后将文档据为己有，如今正被怪异的 events 困扰，几近崩溃。
hp: 12
disposition: obsessive
current_location: miskatonic_university
skills:
  恐吓: 75
  神秘学: 40
  潜行: 40
  侦查: 35
```

## lucy_stone
```yaml
name: 露西·斯通
visible_tags:
  - 二十五六岁
  - 美丽动人
  - 头发漂染成金色
  - 走路摇曳生姿
  - 希布酒馆女服务员
secret: 斯通是莱特的情人，真心爱着他。她知道莱特伪造书籍诈骗的事情，也知道莱特在大西洋城欠下巨额赌债。她保留着古董商阿伯那·维克的名片。两名大西洋城黑帮成员已经威胁过她，命令她一旦有莱特的消息就通知他们。黑帮的来访让她极为恐惧。她是唯一仍然活着且神志正常、能证明莱特行踪的人，因此处境非常危险——多股势力可能想要她的命。
hp: 9
disposition: grieving
current_location: sheb_tavern
skills:
  取悦: 70
  恐吓: 65
  妙手: 65
  潜行: 30
```

## abner_wick
```yaml
name: 阿伯那·维克
visible_tags:
  - 肥胖而有些怯懦的大块头
  - 四十八九岁
  - 喷过量古龙水
  - 脸色苍白病态
  - 绅士风度
  - 古董店"轻率琐事"老板
secret: 维克并非人类——他是一个混血食尸鬼，近百年前由食尸鬼在波士顿停尸房地下诞下。他是食尸鬼与人类的混血，缓慢向完全形态食尸鬼转变。他既食人又杀人，手下赫克托和卡拉·费德曼是服从于他的食尸鬼混血。他的古董店地下室深处有一口通往食尸鬼居住地的砖井，里面堆满人骨。他对克苏鲁神话有深入了解，拥有多种法术。他想要得到女巫审判文档，准备举行仪式安抚怪物并迫使它屈服。
hp: 15
disposition: manipulative
current_location: trivial_pursuits
skills:
  克苏鲁神话: 40
  神秘学: 70
  恐吓: 90
  说服: 70
  估价: 60
spells:
  - 记忆模糊术
  - 食尸鬼联络术
  - 支配术
  - 邪眼术
  - 血肉防护术
  - 枯萎术
  - 折磨术
  - 心理暗示术
```

## ox_and_shanassy
```yaml
name: 奥克斯与肖纳西
visible_tags:
  - 肩膀宽阔
  - 身材壮实
  - 表情严肃
  - 不合身的灰色西装
  - 宽边软呢帽
  - 大西洋城黑帮打手兼讨债人
secret: 他们是莱特的债主派来的打手，莱特欠他们首领11000美元赌债。除非亲眼见到莱特的尸体，否则他们绝不会相信莱特真的死了——即使见到也会怀疑。他们的任务是讨回赌债，但如果发现女巫审判文档的价值，也会转而追寻文档。他们装备有指虎、直剃刀和大口径手枪，会设法孤立目标。他们目前正在监视露西·斯通。
hp: 14
disposition: hostile
current_location: sheb_tavern
skills:
  恐吓: 70
  潜行: 40
  侦查: 40
  锁匠: 40
```

## cecil_hunter
```yaml
name: 塞西尔·亨特
visible_tags:
  - 二十八九岁
  - 瘦削
  - 脸形窄长
  - 满脸雀斑
  - 略微发红的蓬松金发
  - 被紧身衣束缚
secret: 亨特是莱特的造假同伙，在试图复制女巫审判文档中的神秘图示时无意间解开了封印，遭遇怪物首次大规模显形而精神崩溃。他咬断了自己手背的部分肌腱，目前住在阿卡姆疗养院。怪物可能会附身在他身上帮他逃出去。他语无伦次但能通过关键词触发关于真相的片段性描述。
hp: 11
disposition: insane
current_location: arkham_sanatorium
skills:
  艺术/手艺（美术）: 50
  艺术/手艺（造假）: 75
  神秘学: 30
```

## hector_kara_feldman
```yaml
name: 赫克托与卡拉·费德曼
visible_tags:
  - 兄妹
  - 外貌怪异
  - 沉默寡言
  - 服从维克的命令
secret: 他们身上的食尸鬼血统比维克更稀薄，但仍是混血非人生物。他们听从维克的命令进行劳作、绑架、盗窃或杀人。近距离观察需进行理智检定（0/1D4）。他们拥有半胶质表皮护甲。
hp: 14
disposition: submissive
current_location: trivial_pursuits
skills:
  潜行: 60
  追踪: 55
  克苏鲁神话: 30
```

## monster_in_the_ink
```yaml
name: 墨中怪物
visible_tags:
  - 不断流动的庞大暗影
  - 由闪闪发亮红得发黑的蠕虫构成
  - 颜色如沸腾血液与将熄余烬
  - 人类无法理解其知觉
secret: 这是被女巫凯夏·梅森在17世纪90年代束缚在阿卡姆女巫审判文档中的异界存在。它被血与墨封印在书页的角度与文字之中。亨特复制图示的行为释放了它的一部分束缚。它正在冲击现实屏障，每次显形都会获得更多力量和实体。它可以附身在任何触碰过文档的人身上。完全显形后将造成飓风般的巨大破坏和大量死亡。
hp: 43
disposition: hostile
current_location: unknown
special:
  护甲: 5（蠕虫表皮）/ 无实体时免疫物理攻击
  理智损失: 1D4/1D8
```

# 场景 Scene

## miskatonic_university
```yaml
name: 密斯卡托尼克大学
description: 新英格兰最负盛名的高等学府之一，哥特式石砌建筑群坐落于阿卡姆镇中心。校园中充斥着学术氛围，但近来因查尔斯·莱特教授的离奇死亡而笼罩在不安的谣言之中。法伦主任的办公室位于行政楼二层，铺着深色地毯，书架上整齐排列着皮革装订的典籍。
exits:
  - wright_office
  - wright_cottage
  - miskatonic_medical
  - miskatonic_history
  - miskatonic_lodge_office
  - miskatonic_student_commons
  - sheb_tavern
  - trivial_pursuits
  - hobhouse_mansion
  - arkham_sanatorium
npcs_present:
  - bryce_fallon
```

## wright_office
```yaml
name: 莱特的办公室
description: 一间狭小的教职办公室，只有一张书桌、几个文件柜和一个壁炉。墙上挂着一面裂开的镜子——仔细观察会发现它有一部分熔化了。这就是莱特死亡当晚凝视的那面镜子，他看到了恐怖的怪物，怪物燃尽了他的灵魂。隔壁有一间小房间，是艾米莉亚·考特作为助手工作的地方。壁炉里有很多匆忙焚烧的文件残骸。
exits:
  - miskatonic_university
  - miskatonic_history
npcs_present: []
```

## wright_cottage
```yaml
name: 莱特的小屋
description: 校园附近一座面积不大、家具齐全的两层小屋。正门位于南侧面向街道，无后门。一楼为公共功能区（会客/起居/阅读/餐厅/厨房/储藏），二楼为私人空间（卧室/客房/卫生间/楼梯厅）。屋内处于整理到一半的混乱状态——莱特当时正在收拾行李准备离开。休息室壁炉里有匆忙焚烧的文件残骸，桌上堆满各种文件。详细构造见 scenes/莱特小屋构造.md。
detail_file: scenes/莱特小屋构造.md
exits:
  - miskatonic_university
npcs_present: []
```

## miskatonic_medical
```yaml
name: 密斯卡托尼克大学医学院
description: 医学院地下深处冰冷的停尸房。莱特的尸体锁在冷柜中，等待被安静体面地火化。仅仅待在尸体附近就会产生莫名的紧张感，仿佛被监视。聆听成功会听到隔壁空房间里的怪异声响。
exits:
  - miskatonic_university
  - miskatonic_history
npcs_present:
  - john_whitcroft
```

## miskatonic_history
```yaml
name: 密斯卡托尼克大学历史系研究生自习室
aliases: [历史系, 研究生自习室, 主楼西翼三层]
description: 主楼西翼三层靠近楼梯的研究生自习室，旧书、论文和地板蜡的气味混在一起。艾米莉亚·考特通常在靠窗书桌前工作。
exits:
  - miskatonic_university
  - wright_office
  - miskatonic_medical
  - miskatonic_lodge_office
  - miskatonic_student_commons
npcs_present:
  - emilia_court
```

## miskatonic_lodge_office
```yaml
name: 哈兰德·洛奇的历史系办公室
aliases: [洛奇办公室, 历史系教师办公室, 东翼二层]
description: 历史系教师走廊里一间被书籍、未归档论文和私人文件挤满的办公室。
exits:
  - miskatonic_university
  - miskatonic_history
npcs_present:
  - harland_lodge
```

## miskatonic_student_commons
```yaml
name: 密斯卡托尼克大学学生公共休息区
aliases: [学生公共休息区, 学生休息室, 主楼一层阅览室]
description: 主楼一层连接阅览室的学生公共区域，本科生常在课间聚集。安东尼·弗林德斯经常独自在角落查阅宗教与历史资料。
exits:
  - miskatonic_university
  - miskatonic_history
npcs_present:
  - anthony_flinders
```

## sheb_tavern
```yaml
name: 希布酒馆
description: 阿卡姆镇郊一个喧闹但相当高档的场所，以满足镇民的非法饮酒需求而存在。分为两部分：提供软饮料和咖啡的小餐馆（用来掩护），以及提供非法酒水的后屋。露西·斯通在这里做服务员。大西洋城黑帮偶尔在此出没。
exits:
  - miskatonic_university
  - trivial_pursuits
npcs_present:
  - lucy_stone
  - ox_and_shanassy
```

## trivial_pursuits
```yaml
name: 轻率琐事古董店
description: 阿卡姆商业区上等地段一条巷子里的小古董店，外表体面受人尊敬。南侧面向商业街，店面后部为储藏室和办公室，通过后门连接小巷。地下部分从储藏室楼梯向下通往大型储物地下室（旧锅炉/包装箱/废弃家具），再经由隐蔽活板门深入错综复杂的地下隧道网络。深处有食尸鬼窝点、人骨工具和通往地底的砖砌竖井。详细构造见 scenes/维克小店构造.md。
detail_file: scenes/维克小店构造.md
exits:
  - miskatonic_university
  - sheb_tavern
npcs_present:
  - abner_wick
  - hector_kara_feldman
```

## hobhouse_mansion
```yaml
name: 霍布豪斯宅邸
description: 一栋殖民地晚期风格的三层建筑，摇摇欲坠，孤独地矗立在杂草丛生的空地上，距离阿卡姆市区约半小时车程。宅邸覆满灰尘，空无一人。附近的乡间林地极为阴郁荒芜。这里是约书亚·霍布豪斯生前隐居的地方，也是阿卡姆女巫审判文档最初的来源。
exits:
  - miskatonic_university
npcs_present: []
```

## arkham_sanatorium
```yaml
name: 阿卡姆疗养院
description: 一座阴郁的医疗设施，收治精神疾病患者。塞西尔·亨特被关押在这里的住院病房中，穿着紧身衣，受到密切观察。他有时能清醒思考，有时只能病态地呢喃。调查员可以通过特定关键词触发他对真相的碎片化描述。隔壁的软壁病房昨晚还有病人，但今早已经搬空。
exits:
  - miskatonic_university
npcs_present:
  - cecil_hunter
```

# 线索 Clues

## 初始线索
```yaml
- category: investigation
  text: 法伦主任告知：查尔斯·莱特教授死于一场被校方定性为"严重突发心力衰竭"的意外，死亡地点在他的校内办公室。
- category: investigation
  text: 莱特生前正在评估霍布豪斯家族捐赠的阿卡姆女巫审判文档；他死后，这份文档下落不明。
- category: task
  text: 法伦主任愿意借出莱特办公室和住处的钥匙；初步调查可以从办公室、莱特的小屋，或参与文档评估工作的人员开始。
```

洛奇和艾米莉亚不是初始已知线索。调查员向法伦追问参与文档评估的其他人员后，才应记录
二人曾接触相关工作的 NPC 线索。

# 标志 Flags

```yaml
cottage_searched: false
office_searched: false
body_examined: false
documents_recovered: false
double_life_exposed: false
sanatorium_visited: false
hobhouse_explored: false
wicks_shop_searched: false
monster_manifested: false
gangsters_dealt_with: false
monster_defeated: false
```

# 规则 Rules

本模组特有的机械规则——怪物数据、SAN损失、特殊物品、法术。

## monsters
```yaml
- id: ink_monster
  name: 墨中怪物
  description: 被17世纪女巫凯夏·梅森封印在墨水字迹中的异界存在。形态为由粘稠黑色液体构成的蠕动触手团块，中心有一枚发出幽绿光芒的独眼。它通过任何被污染的文本显形——阅读特定手稿的读者会在字里行间看到自己的映像，而后怪物从映像中挣脱。
  attributes:
    STR: 110
    CON: 80
    SIZ: 120
    DEX: 50
    INT: 85
    POW: 75
  hp: 20
  armor: 2 (粘稠保护)
  build: 3
  attacks:
    - name: 触手抽打
      skill: 60
      damage: 1D6 + DB
    - name: 吞噬
      skill: 40
      damage: 2D6 (无视护甲)
  san_loss: 1D4/1D10
  special:
    - 对火焰伤害加倍
    - 半固态躯体可渗入裂缝——不受物理障碍限制
    - 在完全黑暗中获得一个奖励骰于所有攻击

- id: ghoul_aberath
  name: 阿伯那·维克 (混血食尸鬼)
  description: 表面是古董商，实为食尸鬼混血。外表基本人形，但皮肤粗糙偏灰、牙齿略尖。会在夜晚袭击独行者，将受害者割喉切片。
  attributes:
    STR: 85
    CON: 65
    SIZ: 60
    DEX: 60
    INT: 55
    POW: 45
  hp: 15
  armor: 1 (坚韧表皮)
  build: 1
  attacks:
    - name: 利爪
      skill: 50
      damage: 1D6 + DB
  san_loss: 0/1D6
  spells:
    - 尸食术 (Consume Flesh): 吃死者血肉获得其部分记忆，持续1D4小时
    - 接触控制术 (Dominate): 消耗5MP，目标POW对抗，失败则被控制1小时

- id: ghoul_minions
  name: 赫克托与卡拉·费德曼 (食尸鬼混血)
  description: 维克的两名手下，兄妹关系。外表与常人无异但动作异常敏捷，在黑暗中视力极好。
  attributes:
    STR: 65
    CON: 55
    SIZ: 55
    DEX: 70
    INT: 40
    POW: 35
  hp: 11
  armor: 0
  build: 0
  attacks:
    - name: 小刀
      skill: 45
      damage: 1D4 + DB
  san_loss: 0/1D4

- id: cecil_hunt
  name: 塞西尔·亨特 (精神崩溃的造假者)
  description: 莱特的造假同伙。精神已被怪物侵蚀，眼神涣散、喃喃自语、皮肤上有黑色墨水般的纹路。他无意中成了怪物进入这个世界的通道。
  attributes:
    STR: 35
    CON: 30
    SIZ: 50
    DEX: 40
    INT: 65
    POW: 10
  hp: 8
  armor: 0
  build: 0
  attacks:
    - name: 徒手
      skill: 25
      damage: 1D3 + DB
  san_loss: 1/1D4+1 (被墨水侵蚀的脸孔)
  special:
    - 体内寄宿怪物碎片——死亡时墨中怪物从尸体内爆发而出
    - POW只有10，极易被控制或精神攻击
```

## san_triggers
```yaml
- trigger: 第一次阅读女巫审判文档中的手写段落
  severity: 0/1D3

- trigger: 在莱特办公室发现带有烧焦眼球特征的尸体照片
  severity: 0/1D4

- trigger: 目睹墨中怪物从文字/尸体中显形
  severity: 1D4/1D10

- trigger: 在阿伯那·维克的地下室发现被割喉的尸体收藏
  severity: 1/1D6+1

- trigger: 触碰被墨中怪物污染的文本后，在镜中看到自己的倒影变成怪物的眼睛
  severity: 0/1D6

- trigger: 塞西尔·亨特在调查员面前突然死亡，墨水从七窍涌出
  severity: 1/1D8

- trigger: 成功封印/摧毁墨中怪物
  restore: 1D6

- trigger: 发现阿伯那·维克是食尸鬼——他在你面前撕开了一名受害者的喉管
  severity: 1D3/1D8
```

## items
```yaml
- id: witch_trial_documents
  name: 阿卡姆女巫审判文档
  description: 17世纪90年代审判期间霍布豪斯法官与多名当事人的秘密通信。纸张泛黄易碎，以古英语手写。详细记载了凯夏·梅森被指控、审判和处决的过程——以及她死前用血和墨水混合书写的一道"最终证词"。
  category: 关键物品
  effect: |
    阅读需要图书馆使用检定（常规）。成功=了解基本历史。困难成功=识别出凯夏证词中隐藏的拉丁文封印咒语。极难成功=完全理解封印机制并找到逆向执行的方法。
    阅读者需进行一次SAN检定（0/1D3）。

- id: aberrant_seal
  name: 阿伯那的封印徽章
  description: 维克藏在古董店柜台下面的银质徽章，刻有五角星与蛇形环绕图案。背面刻有拉丁铭文"通过此印，关闭通道"。
  category: 关键物品
  effect: |
    持有者可以使用它执行一次封印仪式（需要神秘学检定，困难）。成功可将墨中怪物锁回文本。失败则徽章碎裂，怪物获得一个附加回合。

- id: leitz_diary
  name: 莱特的私人日记
  description: 藏在莱特办公桌暗格中。记录了莱特如何发现霍布豪斯文档中"隐藏的墨水书写层"、塞西尔·亨特的加入、以及他在接触文档文本数日后开始做噩梦的经历。
  category: 线索物品
  effect: 阅读后自动获得线索："莱特日记——通过特殊光照可显现文档隐藏层，内容涉及女巫凯夏·梅森的死亡诅咒"

- id: forged_silhouette
  name: 被污染的拓印本
  description: 塞西尔·亨特制作的阿卡姆审判文档复制件。纸张表面有深黑色墨水晕染痕迹，在烛光下似乎能看到墨迹微微蠕动。触摸时指尖会感到异常的温热感。
  category: 危险物品
  effect: |
    任何持有此物超过1小时者，每晚需进行一次POW检定（常规）。失败=做噩梦，损失0/1 SAN。连续三次失败=墨中怪物通过复制品感知到持有者的位置。
```

## spells
```yaml
- name: 封印墨中怪物
  source: 女巫审判文档隐藏层（图书馆使用，困难成功发现）
  cost: 10 MP + 1D3 SAN
  casting_time: 3轮
  effect: |
    执行者需用银器在墨迹周围刻下封印圆阵，同时口念拉丁文封印咒语。
    仪式技能检定：神秘学 (occult)，困难难度。
    成功=怪物被锁回文本，文本上的墨迹永久凝固。
    失败=消耗翻倍且怪物立即获得一次攻击机会。
```

# 开场 Hook

```
192×年10月，密斯卡托尼克大学行政处主任布莱斯·法伦通过中间人联系上了调查员。
他神情焦虑地讲述了一起令人不安的事件：大学教授查尔斯·莱特在紧锁的办公室里离奇死亡。
官方说法是心脏病突发，但法伦主任并不相信。更糟糕的是，莱特负责评估的一批珍贵文档——
阿卡姆女巫审判文档——如今下落不明。文档的所有者柯布家族正在施压，校园里流传着难听的传言。
法伦需要调查员调查莱特之死的真相，找回失踪的文档——越快越好，越低调越好。
"我不在乎你们用什么方法，"他说，"只要结果干净利落。"
```

# 结局 Endings

## good - 真相大白，怪物被制伏
触发: 调查员找回女巫审判文档，成功摧毁或封印文档中的怪物
描述: 墨中怪物被驱散或彻底消灭。调查员阻止了它在阿卡姆显形造成大规模伤亡的灾难。法伦主任获得了文档（可能不知道危险的部分已被处理），调查员获得了报酬和一位有权势的联系人。每名调查员恢复1D6点理智值。

## neutral - 逃离阿卡姆
触发: 调查员放弃调查，离开阿卡姆
描述: 调查员带着未解之谜离开了阿卡姆。但在数日后的报纸上，他们读到密斯卡托尼克大学发生离奇惨案、多人死亡的头条新闻。怪物在阿卡姆某处显形，造成死伤惨重。调查员需要进行理智检定（1D3/1D10），为自己未能阻止这一切而承受精神创伤。

## bad - 被怪物吞噬
触发: 调查员未能阻止怪物完全显形，或全员理智归零
描述: 墨中怪物完全挣脱了束缚，在阿卡姆中心显形。难以名状的恐怖席卷大学城，街道被蠕虫般的暗影吞没。调查员的意识在无边的恐惧中消散，成为怪物力量的一部分。

## secret - 与维克交易
触发: 调查员将女巫审判文档交给阿伯那·维克，由他处理怪物
描述: 维克利用邪恶的仪式控制了墨中怪物，以牺牲多名受害者满足其毁灭欲望为代价。文档归维克所有，怪物暂时被压制。调查员获得了维克的"感激"，但从此欠下一个危险的非人存在的人情。维克成为了他们未来冒险中一个令人不安的"盟友"。

# 性能摘要（`caddiff diff --perf`）—— 模块功能文档

[English](performance.md) | 简体中文

> 本文件只写**为什么**这样实现，以及**不写下来就会踩的坑**。「代码做了什么」读代码即知；
> 它接进的流水线见 [`pipeline.md`](pipeline.zh-CN.md)（§2 架构、§3 怎么跑、§9 CLI 契约）。

---

## 0. 它是什么、怎么打开

`--perf` 给 `caddiff diff` 增加一步（Step 3/6）：两版进去，出来的是**封闭实体体积**、
**体积加权中心**、**绕整体质心的体积惯量**，以及两版之间的差值与「中心位移按零件分摊」的清单。

| 选项 | 含义 |
|---|---|
| `--perf` | 打开性能步（默认**关闭**，理由见 §1） |
| `--perf-tol T` | 「到底动没动」的相对容差（默认 `1e-06`，乘装配体尺度） |
| `--perf-budget S` | 探针**每一版**的时间预算秒数（默认 `300`） |
| `--density SPEC` | **你已确认**的材料密度，如 `7.85 g/cm3`；不给就一个 kg 都不算 |

产物（只在开了 `--perf` 时出现）：`<out>/perf_snapshot.json`（两版原始数字）、
`<out>/perf_diff.json`（比较结果），以及 `diff_manifest.json` 里的
`summary.performance_diff` —— 报告读的就是它。

**性能块永不改变退出码。** 退出码回答的是「有没有差异」，而性能数字是几何/BOM 变化的
**派生物**：体积或中心动了，几何层早就报了差异。让它参与判定，等于让「改了材质参数」
这种改动把 CI 无缘无故变红。`has_differences` 仍是
`bool(bom_diff_count or geometry_diff_count)`，一个字不改。

## 1. 为什么全部用体积、且默认不算质量

`ShapeMaterial.Density` **不能当默认值用**：实测多份真实公开 STEP 里，所有对象报的都是
FreeCAD Default `1e-09 kg/mm³`（占位值）。照抄着乘下去，就会给出「钢件按水算」的静默错
数字——正是本项目最忌讳的一类失败（看着像数字，其实是错的）。

所以：**不给 `--density`，任何地方都不出现 kg。** `mass_kg_old` / `mass_kg_new` /
`delta_mass_kg` 一律为 `null`，报告里强制显示「材料密度未确认，未计算实际质量。」
给了 `--density 7.85 g/cm3` 才按「体积 × 密度」算，并把来源记进
`settings.density_source = "cli"` 与 `settings.density_kg_per_mm3`，读者永远能判断这个数字
是从哪来的。认的单位：`kg/mm3`（不写单位也按它）、`g/cm3`、`kg/m3`；认不出来直接报参数
错误（退出码 2），绝不猜——`g/cm3` 与 `kg/mm3` 混了就是 1e6 倍。不写单位按 kg/mm³（FreeCAD
的内部单位）读，所以最像人话的错输入 `--density 7.85`（钢的 g/cm³）等于 7.85 kg/mm³——比任何
真实材料致密 1e6 倍。因此一旦取值超过锇（2.3e-05 kg/mm³），运行时会打一条控制台提示：数字
仍按你给的算，但不会悄悄离谱下去。

## 2. 报了哪些量、为什么偏偏是这些

- **体积加权中心，不是重心。** 工具内部所有对象都是单位密度，这个中心是**体积**中心；
  只有当密度均匀时它才等于重心，而工具无从验证这一点。报告里那句
  「体积加权中心（统一密度假设），不是已验证重心。」是**强制**文案，不是修饰。
- **绕整体质心的惯量，不是绕世界原点。** 绕原点的惯量取决于导出时原点放在哪：两版基准
  不同就会显示一个巨大的假变化。绕装配体自身质心的惯量对整体平移不变。张量写在**世界轴**
  下——那正是 `MatrixOfInertia` 已经给你的东西（见 §7 的 F2）。
- **只报六个分量 + 主矩，不报「稳定性」也不报「应力」。** 三维体积惯量（mm⁵）替代不了
  截面惯性矩（mm⁴），所以这里任何东西都不许被叫做失稳/长细比风险；惯量也不是刚度。
  这两句免责说明跟数字一起出。
- **主矩由聚合后的张量求特征值**，绝不把各件主矩逐项相加，也不用
  `PrincipalProperties.Moments` 的轴序（见 §7 的 F5）。
- **贡献清单是精确分摊，不是「占比」。** 对每个零件名：
  `d_i = (V1_i·c1_i − V0_i·c0_i − Cb·(V1_i − V0_i)) / Vb`，`Cb`/`Vb` 取两版中点。
  恒等式 `Σ d_i == C1 − C0` 精确成立（运行时自检，不成立会写成 `degraded_reason`），
  增件/删件天然被吸收，各项可正可负——所以必须有那句「数学分摊、可正负抵消、不是受力或
  重要性排行」。被否决的写法是 `Σ m_i Δc_i`：它漏掉增删件与体积变化，还会制造
  「各项全为正、合计 100%」的假象。
- **主导轴写作 `+X` / `−X` / `+Y` / `−Y` / `+Z` / `−Z`**（U+2212）。前后左右是建模约定，
  本工具无从得知。

## 3. 遍历规则：只加叶子，绝不用容器（实测，故意的）

STEP 装配体导入后是一棵树：一个根 `App::Part` 容器（它的 `Shape` 是全部子件的 Compound）、
每个零件一个 `Part::Feature`、外加一棵 `App::Origin` 基准树（轴/面/点）。

- **全加会重复计数**：容器的 Shape 已经等于子件的并集。实测比值 2.0 / 2.0 / 4.0。
- **只用容器会丢掉大部分信息**：容器是 Compound，而 `Part::Compound` **没有**
  `MatrixOfInertia`（见 §7 的 F6）。实测四份公开真实装配体（Go Kart / Colour-Science-XYZ /
  ekvc 卡丁车 / Voron-0）：容器优先会让每版只剩 **1 个**对象、名字是装配体名——逐零件归因
  消失，而且**惯性张量一个都取不到**。
- **所以规则是：有 `Shape` 且子件里没有任何「可产出」的对象时才取自己；否则下钻。**
  同一批文件上得到 48 / 780 / 233 / 48 个零件，每个都带自己的张量，总量与容器口径相差
  1e-3 以内（叶子之和就是「各零件材料加总」，也与决策 **D-015** 一致——那里已把容器定义为
  子件并集的重复、不参与比对）。
- **兜底必须有**：容器的子件全是基准（什么都产不出）时，仍要用容器**自己**的 Shape，
  否则整份文件会静默变成「0 个对象」。
- 其它类型（`App::Line` / `App::Plane` / `App::Point` / 草图）没有质量属性：既不计入，
  也不许让父对象显得「已被覆盖」。

## 4. 子进程边界、预算与硬死线

质量属性查询跑在 `perf_probe.py` 里，由流水线当独立进程启动，与布尔运算同范式——
**「没有求解器」不等于「查询便宜」**。实测逐对象 19–45 ms，而同一类形状在布尔上实测跑过
840 s 不返回。只有独立进程才能被 kill。

- `--perf-budget` 在探针内部**按版本**生效；没走到的对象一律记为
  `unavailable` + `budget_exceeded`，绝不静默丢弃。
- 父进程的死线是 `2 × budget + 300 s`：探针到预算会自己停，父进程只需再覆盖两次 STEP
  导入这段不受预算约束的开销。
- 探针里两版各开一个临时文档：塞进同一个文档会让 `doc.Objects` 翻倍，而且分不清 old/new。

## 5. 字段契约

### `perf_snapshot.json`

`{"old": <快照>, "new": <快照>}`，每个快照是：

```
side, n_objects, n_zero_volume, n_negative_volume,
total_volume_mm3, total_area_mm2, centroid_mm[3],
inertia_about_centroid_6[6] | null, principal_moments[3] | null, principal_axes[3][3] | null,
bbox_mm[3], objects[], unavailable[], elapsed_s
objects[]  = {label, base_name, volume_mm3, area_mm2, bbox_mm[3], centroid_mm[3],
              centroid_source, tensor_6[6] | null}
unavailable[] = {label, reason}
```

`reason` 是机器读的枚举，**永不翻译**：`no_shape`、`no_centroid`、`volume_unavailable`、
`bbox_unavailable`、`non_finite`、`budget_exceeded`、`non_positive_volume`（计数器沿用契约里的
名字 `n_negative_volume`，统计的是「体积 ≤ 0」，而 reason 写它真正发生了什么）、
`unsupported_type`（类型不在清单里但自带实体的对象——不计数，但绝不静默丢弃）。
`centroid_source` 记录用了哪一级回退（`CenterOfMass` → `CenterOfGravity` →
`StaticMoments/Volume`）——`Part::Compound` 上第一级与第三级都会抛 `AttributeError`。
面/壳对象（没有实体的对象）**只计数**在 `n_zero_volume` 里，**刻意不写名字**到 `unavailable`：
实测有一份装配体有 189 个，诚实性那句只需要这个数，逐个写名字只会把产物撑大而无人消费。
除此之外的每一类排除都带名字。

**只要有一个计入对象没有张量，`inertia_about_centroid_6` 就是 `null`**：只对子集求和等于
给出一个「看着像测量值、其实漏了零件」的数字。主矩在张量缺失或不满足物理有效性判据时
为 `null`（判据见 §7）。

快照还会用**严格的物理上界**自检聚合张量：`tr(I_C) ≤ 2·V·d²`（`d` = bbox 对角线），越界即
打控制台警告。这条自检有真判别力：平行轴项加两遍、或整项漏掉，都会把 trace 顶出上界（坑 F3）；
而体积/一阶矩那两条恒等式在代数上恒真，只能抓「将来有人把总量改成另一条路径」的重构。
实测六对（五份单件 fixture + 两个 44~48 零件的真实装配体）：无一次告警。

### `perf_diff.json`

```
delta_volume_mm3, delta_volume_pct | null,
mass_kg_old | null, mass_kg_new | null, delta_mass_kg | null,
centroid_shift_mm, centroid_shift_components[3], dominant_axis | null,
delta_inertia_6[6] | null, delta_principal_moments[3] | null,
contributors[] = {label, base_name, d_mm[3]},
old{} / new{}  （报告需要的绝对值：体积、面积、质心、惯量、主矩、bbox、
                n_objects、n_not_counted、elapsed_s）,
settings{rel_tol, budget, density_source, density_kg_per_mm3},
degraded_reason | null, unavailable[] = {side, label, reason}
```

旧版总量为 0 时 `delta_volume_pct` 是 `null`：近零基准没有有意义的百分比，写 `0` 或 `∞`
都是错数字。

`degraded_reason` 正常为 `null`，否则是 `no_volume`（没有体积可分摊）或
`contributor_allocation_mismatch`（分摊恒等式不成立——如实报降级，不藏）。
`unavailable[].reason` 在快照枚举之外还有 `no_inertia_tensor` 与 `no_principal_moments`。

`contributors` 按对象 **label** 归并（同名相加），所以改名零件会显示成两条而不是被静默
合并；同时带着族名 `base_name`，便于与按名配对的几何层对照。

## 6. 报告怎么用它

报告只读 `summary.performance_diff`，**不**去读 `perf_diff.json` / `perf_snapshot.json`
（那会造成两套口径）。

- **只有真的存在的量才出行**：张量取不到就没有惯量行，位移在容差内就没有主导轴行。
  缺的量显示成 `0` 会被读成「算出来是 0」。
- 诚实性那句「仅基于 {n} 个封闭实体；{m} 个面/壳对象未计入。」放进「未参与比对 / 未解析」
  一节：`n` = **新版**计入的实体数，`m` = `n_zero_volume + len(unavailable)`（面/壳、
  退化实体、无形状对象、非有限值对象、类型不在清单里的对象、预算内没走到的对象）。
  当 `m > 0`，或一个对象都没计入时才显示。注意那个名词：句子写的是「面/壳对象」，而 `m`
  刻意统计**每一个**被排除的对象——措辞是 plan 定稿的固定文案，取值则选成**永不漏报**；
  逐个对象的真实原因在 `perf_snapshot.json` / `perf_diff.json` 里。
- 免责说明的出现条件：「材料密度未确认…」只在 `settings.density_source != "cli"` 时出；
  重心与刚度两句总是出；分摊那句只在确实列了贡献清单时出。
- `degraded_reason` 非空时（分摊恒等式没通过、或没有体积可分摊）**不展示**贡献清单：
  一个没验证过的「谁是主犯」比没有更糟。原因留在 `perf_diff.json`，运行时也会在控制台喊一声。
- 本次结论是**无差异**而性能块存在时，该节开头会加一句「几何与 BOM 未变；下方性能数字的
  差异在配置容差内。」——否则读者会看到「无差异」和一组看似矛盾的数字。
- 性能块整块读不回来时（键在、内容为空），运行时会打一条控制台提示——否则人读产物会与
  「根本没开 `--perf`」一模一样。

## 7. 踩过的坑（全部实测；不要"改回去"）

- **F2 —— `MatrixOfInertia` 已是世界系、且是绕对象自身质心的。** 嵌套 `Placement` 已合成。
  再乘一次 `R·I·Rᵀ` 就是把旋转做两遍。用 200×20×20 的梁绕 Z 转 45° 的解析解验过。
- **F3 —— 张量必须两遍累积。** 先求整体质心 `C`，再按 `d = c_i − C` 加平行轴项。增量式累加
  （每轮参考点不同）实测让 `Jyy` 偏 `M·50²`。
- **F5 —— `PrincipalProperties.Moments` 是降序返回的**，与 `A11/A22/A33` 的轴序不同
  （误用实测差 50 倍）。主矩必须由**聚合后**张量求特征值，各件主矩不能相加。
- **F6 —— `Part::Compound` 上 `CenterOfMass` / `StaticMoments` / `MatrixOfInertia` / `Mass` /
  `PrincipalProperties` 全抛 `AttributeError`**，而 `CenterOfGravity` / `Volume` / `Area` 可用。
  这就是三级质心回退与「张量可为 null」的来源。
- **F7 —— 真实文件里有大量非实体对象**：实测一份 193 对象的总成是 4 个实体 + 189 个零体积
  面/壳（±1e-16），另有负体积退化件与取不到质心的对象。它们全部被计数并如实报告；
  负体积**绝不**取 `abs()`——取绝对值等于把一个坏实体伪装成正常实体。
- **F8 —— 密度陷阱**（见 §1）。
- **张量有效性判据必须带相对容差。** 真实惯性张量满足 `λ_i ≤ λ_j + λ_k`，但细长杆恰好取
  等号，用严格小于会把合法零件判成坏张量。判据用相对容差；不满足时报「不可用」，
  而不是取绝对值（那会把坏张量伪装成合法结果）。

## 8. 已知限制（刻意的）

- **材质/密度变更不在覆盖范围内**：受众的 STEP 通常不带材料信息。`--density` 描述的是整个
  装配体的一个密度，两版换了材料这件事这样查不出来。
- 导入后**只有一个对象、且它没有子对象**的文件（一个 Compound 里装多个实体，如融合成
  一体的多实体零件）会被当成一个零件：逐零件归因与惯性张量在那时不可用。没有去拆这个
  Compound——拆出来的实体没有零件名可归。这条说的是 STEP 导入器产出的对象
  （`Part::Feature` / `App::Part`，包括 Shape 是 Compound 的那种）。**类型不在清单里**却
  自带实体的对象（裸 `Part::Compound`、`PartDesign::Body`）刻意**不计数**——把 Body 与它的
  tip 特征都算上就是重复计数——但会以 `unsupported_type` 记入「未计入」，绝不静默丢弃。
- 预算内没走到的对象记为「未计入」，不做估算。
- 启发式性能指标（长细比、应力集中几何）与求解器对比（T1/T2）**不在范围内**：前者需要截面
  惯性矩与失稳模型；后者要求两版同一物理工况且网格误差受控，并且已明确不放进本仓。

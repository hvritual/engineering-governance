# CG-00：外置治理执行章程

状态：**R5 PARTIAL / BLOCKED；Issue #1 保持 OPEN；CG-01 未启动。**

本版将 R1–R4 的历史观察移回 Git 历史，正文只描述当前事实，不把已经解除的工具链/源码阻断继续当作现状。历史四文档快照：`6b7609c9df35524ea4e4378b4d7ce855b1021ca9`，tree `22f8749fbac4739e56a3ab29fe305fe78f009be1`。原始本地历史保存在交付 bundle；Contents/Git Data API 发布的远端提交身份另行记录。

## 1. 授权、目标与非目标

用户已授权将四份治理文档公开发布到 `hvritual/engineering-governance`，并要求沿 Issue #1 补齐实际命令，验收后才关闭 CG-00、进入 CG-01。公开发布不再是阻断，不重复请求授权。

CG-00 仅冻结并验证治理资产位置、五个源码角色、消费者 CLI/生成器/Runtime 来源、文件边界与命令前提。只更新四份 G 文档，不新增 Yunka 命令、通用规则引擎或新的脚手架。允许后续按问题选用独立工具/专用脚手架，不要求全部融入 Yunka。

禁止修改框架、消费者代码/依赖/pin/generated、`server/**`、工作流或权限。原始日志、源码和凭据不公开提交；本轮公开内容只含状态、来源标识、命令说明及摘要。

## 2. 固定输入不变

| 角色 | Commit | Tree |
|---|---|---|
| 框架参考 | cc546c7d25ee316442eafacd3c93599f7691f22a | 84ef1130c3aead3fef83b990951790b6d996d2a0 |
| Biz | fed9051bae618ab28df200038682f808c82e854b | 5883f6783dbab1336fa25b36adb1f7e938086540 |
| Biz 相邻 Yunka | 33b98ceba57494abda2299e4f0290a5651dab4bc | fcc154390dc3a24ebb1819954fc883d0dd073c31 |
| IoT | bcd20632b666405c3f7a8fe8d53f591c78450087 | 0c0d1a2da23de4b7315245faba2103ec7a64377e |
| IoT 子模块 Yunka | 057ebcf88a87303eb633eb6e604d306f633dfac0 | b2d82c574f6db1950b4323db839feda2d6fbc898 |

框架参考不是消费者升级目标。Biz 的相邻 replace 与 IoT 的 gitlink 分别验证；不能共享一个最新 CLI/源码副本冒充两个已锁定来源。

## 3. R5 实际完成

五个角色的完整当前源码树均已恢复。逐角色执行 HEAD、tree、对象类型、Git fsck、工作树清洁和浅历史查询，结果与固定输入一致。Biz 使用完整源码归档加原始签名提交对象恢复，**显式浅历史**：当前树完整，父提交历史未导入；其余四角色非浅历史。需要祖先比较的后续任务不能把 Biz 当作完整历史。

Go 1.25.13 已通过 `actions/go-versions` 制品恢复并在当前容器实际执行。安装包为 GitHub Actions repack，不是 go.dev 原始 tarball；来源、包摘要和二进制摘要分别核对，没有混用。

本地实际通过：Biz 的既有源码锁核验脚本、Biz 四个指定模块解析、IoT 子模块/Go 前置检查、IoT 当前 module 的指定依赖解析。legacy module 的命名查询虽退出 0，但 go-kit 替换目录缺失，未判为模块验收通过。五个源码角色的 60 个依赖/锁文件记录前后摘要一致，最终工作树均清洁。

另外，重跑既有 IoT Go 诊断 job：run `34223977001` / attempt `2` / job `103079287635`，固定 head `bcd20632…`。实际 `go mod tidy -diff`、Go test、Go vet、race 四命令退出码均为 0，原始制品已下载核验。该 run 中继承的旧 canonical-full、web、browser 成功记录不计为本轮执行。

## 4. 当前仍未通过

| 必需项 | 实际状态 |
|---|---|
| 五个当前源码树/对象/clean | PASS；Biz 浅历史边界单列 |
| Go 1.25.13 实际版本 | PASS |
| Biz / IoT 当前模块的本地替换核对 | PASS，不等于完整依赖图/行为资格 |
| IoT legacy 模块 | INCOMPLETE；go-kit 替换目录不存在；命名查询 exit 0 不构成通过 |
| Biz 完整 workspace 解析脚本 | 已执行，退出 1；缺第三方模块缓存 |
| 两消费者顶层 help、context help、context JSON | 六次真实尝试均在依赖加载阶段退出 1，尚未进入 CLI；BLOCKED |
| 消费者依赖文件不变 | PASS，60 个角色/路径记录 |
| CG-00 总体验收 | BLOCKED |

离线运行设置 `GOPROXY=off`；另行使用 `https://proxy.golang.org` 的真实下载探测仍因 DNS 失败，因此不能靠更换该环境变量获得依赖。Biz 首个缺失项为 `github.com/go-kit/log@v0.2.1`，IoT 为 `github.com/buger/jsonparser@v1.6.1`；这不是完整缺失清单，也不是新框架缺陷。

## 5. 恢复路径与停止条件

下一次直接复用已核验源码与 Go，不重新寻找同一批制品。恢复受信任、与锁定 module/sum 一致的离线依赖缓存，或使用获准且具备依赖的既有只读云端入口；不得通过替换版本、关闭验证、修改消费者或新增未授权工作流绕过阻断。

补齐六个 help/context 命令及 Biz workspace 脚本，核对 JSON 实际内容、模块 Version/Replace.Dir、源码身份和文件前后摘要。所有必需项成立后，更新四文档并回读实际 main/tree，再关闭 Issue #1、开启 CG-01。命令失败证据已保存，不将其记为 NOT_RUN 或 PASS。

## 6. 证据与发布

R5 原始命令、退出码、工具包校验、源码恢复过程和交付回执保存在会话交付包。核心记录：`evidence/fixed-source-actual-commands.json`，SHA-256 `5a4b131f4e7c42db53abb52b70f823d101264fed022a20895303f64aa3107c89`。其中 54 条本地命令为 47 次退出 0、7 次退出 1；这不是 54 个验收项，不能用总数掩盖关键失败。

公开来源：Issue https://github.com/hvritual/engineering-governance/issues/1 ；新执行 job https://github.com/hvritual/iot-delivery-system/actions/runs/34223977001/job/103079287635 。归档制品只用于源码/工具传输，历史测试结果不继承为本轮资格。

本版的实际提交/tree 由发布后回执记录，避免在文件中自引用尚未生成的 commit。远端发布成功不提升消费者资格。

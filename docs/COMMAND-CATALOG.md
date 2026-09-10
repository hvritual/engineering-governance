# CG-00 命令目录与实际执行状态

状态：**R5 PARTIAL / BLOCKED**。源码和 Go 已恢复；两个消费者 CLI 的 help/context 六次尝试及 Biz workspace 核验仍因缺模块缓存失败，不是已完成验收。

## 1. 来源与执行条件

命令来源为各固定消费者的 Makefile、源码锁核验脚本及已有工作流。原 R4 来源/静态命令目录保存在 `6b7609c9df35524ea4e4378b4d7ce855b1021ca9`。当前实际原始记录为 R5 交付包的 `evidence/fixed-source-actual-commands.json`，记录精确 argv、CWD、开始/结束时间、选定环境、stdout、stderr、退出码；源码读取不冒充执行。

以下变量均对应 R5 已恢复的真实工作区：

```bash
S="$CG_ROOT/sources"
GO125="$CG_ROOT/toolchain/go-1.25.13/bin/go"
B="$S/biz"
B_YUNKA="$S/yunka.io"
I="$S/iot-delivery-system"
I_YUNKA="$I/third_party/yunka"
```

实际 Go 环境：`GOTOOLCHAIN=local`、`GOENV=off`、`GOFLAGS=-mod=readonly`，GOROOT unset；缓存与临时目录位于消费者根外。离线批次为 `GOPROXY=off`、`GOSUMDB=off`，不表示允许未校验依赖。单独启用官方代理/校验服务的下载探测仍因 DNS 失败；未取得模块字节。

## 2. 五角色 Git 身份：PASS

<!-- CG00:GIT-IDENTITY:BEGIN -->
```bash
git -C "$R" rev-parse --verify HEAD
git -C "$R" rev-parse --verify 'HEAD^{tree}'
git -C "$R" status --porcelain=v1 --untracked-files=all
```
<!-- CG00:GIT-IDENTITY:END -->

另执行 `git cat-file -t <commit>`、`git cat-file -t <tree>`、`git fsck --full`、`git rev-parse --is-shallow-repository`。五个角色均返回固定身份、正确对象类型、clean 和 fsck 成功。Biz shallow=true，其余 false；不隐瞒历史范围。

## 3. Go：PASS

```bash
"$GO125" version
"$GO125" version -m "$GO125"
"$GO125" env GOROOT GOVERSION GOOS GOARCH
```

实际版本 `go1.25.13 linux/amd64`。GitHub Actions repack 来源、外层 ZIP/内层 tar/二进制摘要见 BASELINES.json；未把 repack 摘要与 go.dev 原始包摘要混同，也未替换原系统 Go。

## 4. 本地来源与模块解析：部分通过

| 实际命令（省略已记录的环境前缀） | CWD | 结果 |
|---|---|---|
| bash scripts/verify-yunka-source.sh | Biz | 0，锁定 33b98ceb…且实际 Replace.Dir 相符 |
| go list -mod=readonly -m -json yunka.io/framework yunka.io/gateway yunka.io/pkg github.com/go-kit/kit | Biz | 0，四个指定模块 |
| bash scripts/consumer-resolution-check.sh | Biz，使用 Biz go.work | 1，缺少完整依赖图缓存 |
| make yunka-revision-check | IoT/backend-yunka | 0 |
| make yunka-cli-check GO=<GO125> | IoT/backend-yunka | 0，只证明源码/Go 前置，不等于 CLI 已运行 |
| go list -mod=readonly -m -json github.com/hvritual/yunka.io/framework github.com/hvritual/yunka.io/gateway github.com/hvritual/yunka.io/pkg | IoT/backend-yunka | 0，指定模块解析 |
| go list -mod=readonly -m -json yunka.io/framework yunka.io/pkg github.com/go-kit/kit | IoT/backend | 查询 0，但目录检查发现 go-kit replace 缺失；INCOMPLETE |

三个 `go list` 的实际 Version、Replace.Path、Replace.Dir 已保存。随后对 10 个返回目录执行存在性检查：9 个存在、1 个不存在；缺失项为 legacy `github.com/go-kit/kit` 的 `third_party/yunka/compat/go-kit-kit-log/go.mod`。两个 legacy Yunka 名称与目标 go.mod 的 github.com 路径声明不同，只记录事实、不声称已兼容。详见私有 `resolved-directory-validation.json`。60 个依赖/锁文件角色记录前后 SHA-256 相等；命令结束后五个角色工作树再次检查为 clean。

## 5. 必需 CLI 命令：六次真实尝试，全部 BLOCKED

Biz 在 `$B_YUNKA/app`、`GOWORK=$B_YUNKA/go.work`；IoT 在 `$I_YUNKA/app`、`GOWORK=$I_YUNKA/go.work`，分别执行：

```bash
"$GO125" run ./cmd --help
"$GO125" run ./cmd context --help
"$GO125" run ./cmd context --root "$CONSUMER_ROOT" --json
```

Biz 三次 exit 1，首个缺失模块 `github.com/go-kit/log@v0.2.1`；IoT 三次 exit 1，首个缺失模块 `github.com/buger/jsonparser@v1.6.1`。失败发生在依赖加载/构建阶段，尚无 CLI 响应或 context JSON，不虚填二进制摘要，不判为框架不支持参数。

另真实执行 `go mod download github.com/buger/jsonparser@v1.6.1`，启用 `GOPROXY=https://proxy.golang.org`、`GOSUMDB=sum.golang.org`，仍 exit 1，DNS 连接被拒；不能归因于单纯主动设置 GOPROXY=off。

## 6. 新鲜托管执行：四命令 PASS，但范围有限

既有 run `34223977001` 的 Go 诊断 job 重跑 attempt 2，job `103079287635`；固定 head `bcd20632b666405c3f7a8fe8d53f591c78450087`。在 `backend-yunka`、Go 1.25.13、GOWORK off 下：

```bash
go mod tidy -diff
go test -mod=readonly -count=1 -timeout=10m ./...
go vet -mod=readonly ./...
go test -mod=readonly -race -count=1 -timeout=10m ./...
```

原始 TSV 四个退出码均为 0，test/race 日志有实际包通过输出；head/submodule 正确，worktree.txt 和 patch 均为空。制品 `10176886196`、SHA-256 `76c4f1e2fa57856827d61383b438dcdd73e04d6f0d1fb7abc255be8859d8a852` 已按实际 ZIP 字节核验。

同一 latest-attempt 列表里的 canonical-full/web/browser 作业仍保留旧执行时间，不计入本轮。此次 Go 诊断没有执行以上六个 CLI 命令，不能替代它们。

## 7. 续跑清单

恢复受信任的离线 Go 依赖或获准的现有只读执行环境后，先核对当前五角色和工具/模块身份，再执行第 4 节的 Biz workspace 脚本和第 5 节六命令。保存真实 JSON/帮助输出并检查内容，不仅检查退出码；复核依赖摘要与 clean。

不要运行 `make generate`/`yunka-generate`、`go get`/`go mod tidy` 写入版本；不要新增工作流或取消门禁；不要把历史日志作为本轮执行。补齐后还必须完成四文档发布的 main/tree 回读，才可关闭 CG-00 并进入 CG-01。

# v0.3.0 修复工作副本

本副本来自已封存的 v0.2.0，修复在独立目录进行。v0.2 原始输入和实测结果不变。
v0.3 使用计划 schema 3，旧计划必须用原版审查；禁止转换或混合旧结果。
`results/` 和原有 AUDIT/DESIGN 文档是 v0.2 历史材料，新修复证据单独交付。

## 主要变化

- Docker 的检查、启动和清理统一使用绝对路径。受信 worker 用专门的执行路径变量传递解析结果，候选环境继续保持最小化。
- 受信 `/bin/sh` 包装器在候选运行前输出一次握手，控制器确认后才发送候选输入。启动、候选执行和清理分别计时，启动默认 30 秒、执行默认 8 秒、编译默认 120 秒；全部进入计划并实际传递。执行用时仍包含握手后 Docker 客户端结束的延迟，不是纯 CPU 时间。
- 启动失败/启动超时与候选运行失败区分。候选已开始后退出 125/126/127 保留为候选错误；清理失败明确记录为基础设施错误。
- 评分前先原子保存输入及提交。预算失败、上游错误保留部分文件，受控写入后保存检查点。每阶段更新进度；中断的 `RUN-STATE.json` 保持 `running`，不能进入完整运行审查。
- 阶段记录、源码产物、输入、检查点、请求收据和磁盘交接证据纳入封存。部分产物不能计成功；同源 T0 维护分叉继续检查。
- 预算配置可由 CLI 读取；预算档名称、校准状态、模型/推理档及可选客户端文件哈希进入冻结计划。客户端文件在运行前后核验。默认原预算和附带 Codex 诊断预算均未被本次修复校准。
- Codex 适配器已纳入源码，仍通过 APIZN、`APIZN_API_KEY`、medium、受控公共文件工具和离线 Docker 执行。CLI 按既有要求为 `danger-full-access`，模型无原生环境访问。凭据只从环境读取。

## 本地测试与镜像

Python 3.11+、pytest、Node 22.16.0、TypeScript 5.8.3、Go 1.23.2。当前已准备实例可使用：

```bash
source /workspace/changebench-validation/activate.sh
cd /workspace/changebench-repair
python -m pytest -q
```

容器构建只复制已安装的固定工具链，不在镜像内联网安装。三个镜像共用固定的 Python 基础摘要，TS/Go 仅加入各自工具链；它们不是跨语言耗时排名的依据，也不声称字节数达到绝对最小。
提前准备 `containers/Dockerfile.*` 中的基础镜像，然后构建：

```bash
python scripts/build_runtime_images.py --out /tmp/cb-images.json
CB_REPAIR_IMAGES=/tmp/cb-images.json CB_REPAIR_RESULTS=/tmp/cb-docker-evidence \
  python -m pytest -q integration --junitxml=/tmp/cb-docker-tests.xml
```

集成检查缺少镜像或 Docker 时失败，不静默跳过或切回宿主机。包含三语言编译/执行、真实退出 127、死循环、实际 T0 磁盘分叉、第二次重启、破坏升级格式的负向控制、私有权限状态与符号链接拒绝，以及完整轨迹审查。

## 冻结预算配置

```bash
python -m changebench plan --suite poly --languages python --levels 1 \
  --repeats 1 --budget-config examples/codex-budget-diagnostic.json --out /tmp/cb-plan.json
```

这个命令不调用模型。示例把 L1 调成 60k，仅用于继续诊断；不能声称所有 L1 维护、语言或 L4 已有合适预算。正式校准应与效果试验分开，并在查看组间效果前定义选择规则。

`client_profile.artifacts` 可加入绝对路径到 SHA256 的映射，例如实际 Codex 可执行文件、模型目录及适配器文件。预算档必须随这些配置变化重新冻结。服务商未证明的模型不可变快照仍未知。

Token 上限是阶段终止规则。Codex 在收到上游收据后才能检查消耗，可能单请求超额；不保证严格计费封顶。报告保留实际消耗和超额，未知总量为 null，已知逐请求用量另存。没有验证当前 app-server 提供精确逐请求 Token 准入机制，因此未伪造硬封顶功能。

## 可选的真实磁盘升级契约

在 `plan` 命令添加 `--persistent-upgrade`。模型在 T0 与维护阶段均收到明确的兼容要求；这是新契约，不追加到 v0.2 旧评分中。

T0 在真实私有目录写入种子数据，停止进程后由受信转移程序在离线容器内捕获。相同冻结数据分别恢复到 local/extension 独立目录，再次捕获核验相同后才启动维护候选。保留原值/版本、幂等回执、已消费消息不复活和继续操作/重启的检查。

该档约定状态只含普通文件和目录，总文件数据最多 256KiB、200 个文件、256 个目录；链接、特殊文件或超限为不支持的提交。宿主机不遍历候选创建的内容；捕获、恢复和清理在受信容器内完成。此范围仍不涵盖并发、断电一致性或任意文件系统元数据依赖。

Docker 运行配置使用 `evaluation_images` 映射。若选择了持久任务且没有 Python 候选镜像，应额外提供 `state` 键，指向可信 Python 状态转移镜像。镜像 ID 在任何模型调用前解析并验收。

## 后续实测

先完成运行器门禁，再用新冻结预算进行小规模 A/B/C 试跑。已观察过的四个族可用于回归/校准，新的效果验证需要独立重复和新的留出证据。本次代码修复及容器验证不构成新的模型效果试验。

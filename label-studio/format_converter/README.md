# 格式转换器 (Format Converter)

基于 `label-studio-converter` 的独立格式转换 Docker 镜像，用于将增强后的数据集转换为各种标准格式。

## 🎯 功能特性

- **多格式支持**: YOLO、COCO、Pascal VOC、CSV
- **复用 Label Studio 转换逻辑**: 使用 `label-studio-converter` 库
- **Pachyderm 集成**: 作为独立流水线步骤运行
- **完整错误处理**: 详细的日志和错误报告

## 📦 镜像构建

```bash
# 构建镜像
chmod +x build.sh
./build.sh

# 或者手动构建
docker build -t format-converter:latest .
```

## 🚀 使用方法

### 本地测试

```bash
docker run -v /path/to/input:/pfs/augmented_dataset \
           -v /path/to/output:/pfs/out \
           format-converter:latest \
           python3 /app/converter.py --format YOLO
```

### Pachyderm 流水线

使用提供的流水线配置文件：

```bash
# 创建 YOLO 格式转换流水线
pachctl create pipeline -f yolo-converter-pipeline.json

# 创建 COCO 格式转换流水线  
pachctl create pipeline -f coco-converter-pipeline.json

# 创建 VOC 格式转换流水线
pachctl create pipeline -f voc-converter-pipeline.json
```

## 📁 目录结构

```
format_converter/
├── Dockerfile              # Docker 镜像定义
├── converter.py            # 主转换脚本
├── build.sh                # 构建脚本
├── yolo-converter-pipeline.json   # YOLO 流水线配置
├── coco-converter-pipeline.json   # COCO 流水线配置
├── voc-converter-pipeline.json    # VOC 流水线配置
└── README.md               # 本文档
```

## 🔧 配置说明

### 环境变量

- `FORMAT_TYPE`: 目标格式 (YOLO/COCO/VOC/CSV)

### 输入要求

输入目录应包含：
- 增强后的图片文件 (.jpg, .png, .bmp)
- 对应的标注文件 (.json)

### 输出格式

- **YOLO**: classes.txt + 图片 + .txt 标注文件
- **COCO**: annotations.json + 图片
- **VOC**: XML 标注文件 + 图片
- **CSV**: annotations.csv + 图片

## 🐛 故障排除

### 常见问题

1. **转换失败**: 检查输入数据格式是否正确
2. **内存不足**: 增加流水线的内存限制
3. **权限问题**: 确保容器有读写权限

### 日志查看

```bash
# 查看流水线日志
pachctl logs --pipeline format-converter-yolo

# 查看作业日志
pachctl logs --job <job-id>
```

## 🔄 数据流

```
增强数据 → 格式准备 → label-studio-converter → 目标格式 → 输出
```

## 📋 依赖库

- `label-studio-converter`: 核心转换功能
- `python-pachyderm`: Pachyderm 客户端
- `Pillow`: 图像处理
- `numpy`: 数值计算
- `opencv-python-headless`: 图像处理
- `lxml`: XML 处理
- `xmltodict`: XML 转换

## 🚨 注意事项

- 确保输入数据格式与 Label Studio 标注格式兼容
- 大数据集转换时注意内存使用
- 定期清理临时工作目录
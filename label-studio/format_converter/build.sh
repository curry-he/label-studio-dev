#!/bin/bash
# 格式转换器 Docker 镜像构建脚本

set -e

# 配置
IMAGE_NAME="format-converter"
IMAGE_TAG="latest"
REGISTRY="localhost:5000"  # 替换为你的镜像仓库地址

# 构建镜像
echo "🔨 Building format converter Docker image..."
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .

# 标记镜像
echo "🏷️  Tagging image for registry..."
docker tag ${IMAGE_NAME}:${IMAGE_TAG} ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}

# 推送镜像（可选）
read -p "📤 Push image to registry? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "📤 Pushing image to registry..."
    docker push ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}
    echo "✅ Image pushed successfully!"
else
    echo "ℹ️  Skipping push. To push later, run:"
    echo "   docker push ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
fi

echo "🎉 Build completed!"
echo "Image: ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
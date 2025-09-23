import { useEffect, useRef, useState } from "react";
import { Button } from "../../../components";
import { Form, Input } from "../../../components/Form";
import { Modal } from "../../../components/Modal/Modal";
import { Space } from "../../../components/Space/Space";
import { useAPI } from "../../../providers/ApiProvider";
import { BemWithSpecifiContext } from "../../../utils/bem";
import "./VersionExportModal.scss";

const { Block, Elem } = BemWithSpecifiContext();

const downloadFile = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'export.zip';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};

export const VersionExportModal = ({ visible, onHide, project, version }) => {
  const api = useAPI();
  const [downloading, setDownloading] = useState(false);
  const [downloadingMessage, setDownloadingMessage] = useState(false);
  const [availableFormats, setAvailableFormats] = useState([]);
  const [currentFormat, setCurrentFormat] = useState("JSON");

  /** @type {import('react').RefObject<Form>} */
  const form = useRef();

  const proceedExport = async () => {
    setDownloading(true);

    const message = setTimeout(() => {
      setDownloadingMessage(true);
    }, 1000);

    const params = form.current.assembleFormData({
      asJSON: true,
      full: true,
      booleansAsNumbers: true,
    });

    try {
      // 使用新的 Dataset Version 导出 API
      const createResponse = await api.callApi("createDatasetExport", {
        params: {
          pk: project.id,
          versionId: version.id,
        },
        body: {
          format: currentFormat,
          ...params,
        },
      });

      // 检查响应是否存在且有效
      if (createResponse && !createResponse.error) {
        const { id: exportId, status: exportStatus, download_url } = createResponse;
        
        if (exportStatus === 'completed' && download_url) {
          // 导出即时完成，直接下载
          setDownloadingMessage("导出完成，正在下载...");
          window.open(download_url, '_blank');
          onHide();
        } else if (exportStatus === 'processing') {
          // 如果仍在处理中（不太可能），开始轮询状态
          setDownloadingMessage("正在准备导出文件...");
          pollExportStatus(exportId);
        } else {
          // 其他状态的处理
          console.error('Unexpected export status:', exportStatus);
          setDownloadingMessage("导出状态异常，请稍后重试");
        }
      } else {
        // 处理API错误响应
        const errorMessage = createResponse?.error || createResponse?.message || '导出请求失败';
        console.error('Export API error:', createResponse);
        
        // 提供更友好的错误信息
        if (errorMessage.includes('尚未处理完成')) {
          setDownloadingMessage("数据集版本正在处理中，请等待处理完成后再导出");
        } else if (errorMessage.includes('没有有效的输出数据')) {
          setDownloadingMessage("数据集版本处理失败，请重新处理版本");
        } else {
          setDownloadingMessage(`导出失败: ${errorMessage}`);
        }
        
        // 如果有具体的错误处理器，使用它
        if (createResponse && typeof api.handleError === 'function') {
          api.handleError(createResponse);
        }
      }
    } catch (error) {
      console.error('Export error:', error);
      setDownloadingMessage("导出请求出错，请稍后重试");
      
      // 安全地调用错误处理器
      if (error && typeof api.handleError === 'function') {
        try {
          api.handleError(error);
        } catch (handlerError) {
          console.error('Error handler failed:', handlerError);
        }
      }
    }

    setDownloading(false);
    setDownloadingMessage(false);
    clearTimeout(message);
  };

  const pollExportStatus = async (exportId) => {
    const maxAttempts = 30; // 最多轮询 5 分钟（每10秒一次）
    let attempts = 0;

    const checkStatus = async () => {
      try {
        const statusResponse = await api.callApi("checkExportStatus", {
          params: {
            pk: project.id,
            versionId: version.id,
            exportId: exportId,
          },
        });

        if (statusResponse && !statusResponse.error) {
          const { status: exportStatus, progress, download_url } = statusResponse;

          if (exportStatus === 'completed' && download_url) {
            // 导出完成，提供下载链接
            setDownloadingMessage("导出完成，正在下载...");
            window.open(download_url, '_blank');
            onHide();
            return;
          } else if (exportStatus === 'failed') {
            api.handleError({ message: statusResponse.error_message || '导出失败' });
            return;
          } else if (exportStatus === 'processing') {
            // 更新进度
            setDownloadingMessage(`导出进度: ${progress || 0}%`);
            
            attempts++;
            if (attempts < maxAttempts) {
              setTimeout(checkStatus, 10000); // 每10秒检查一次
            } else {
              api.handleError({ message: '导出超时，请稍后重试' });
            }
          }
        } else {
          api.handleError(statusResponse);
        }
      } catch (error) {
        console.error('Status check error:', error);
        api.handleError(error);
      }
    };

    checkStatus();
  };

  useEffect(() => {
    if (visible && project?.id) {
      // 对于Dataset Version导出，我们主要提供YOLO格式
      const datasetVersionFormats = [
        {
          name: "YOLO",
          title: "YOLO Format", 
          description: "YOLO darknet format with labels in separate .txt files",
          tags: ["Computer Vision"],
          disabled: false
        },
        {
          name: "JSON",
          title: "JSON",
          description: "Raw Label Studio JSON format", 
          tags: ["General"],
          disabled: false
        }
      ];
      
      setAvailableFormats(datasetVersionFormats);
      setCurrentFormat("YOLO"); // 默认选择YOLO格式
    }
  }, [visible, project?.id]);

  if (!visible) return null;

  return (
    <Modal
      onHide={onHide}
      title={`Export Dataset Version: ${version?.name || version?.id}`}
      style={{ width: 720 }}
      closeOnClickOutside={false}
      allowClose={!downloading}
      visible
    >
      <Block name="version-export-modal">
        <FormatInfo
          availableFormats={availableFormats}
          selected={currentFormat}
          onClick={(format) => setCurrentFormat(format.name)}
        />

        <Form ref={form}>
          <Input type="hidden" name="format" value={currentFormat} />
        </Form>

        <Elem name="footer">
          <Space style={{ width: "100%" }} spread>
            <Elem name="recent">{/* Previous exports could go here */}</Elem>
            <Elem name="actions">
              <Space>
                {downloadingMessage && (
                  typeof downloadingMessage === 'string' 
                    ? downloadingMessage 
                    : "Files are being prepared. It might take some time."
                )}
                <Elem tag={Button} name="finish" look="primary" onClick={proceedExport} waiting={downloading}>
                  Export
                </Elem>
              </Space>
            </Elem>
          </Space>
        </Elem>
      </Block>
    </Modal>
  );
};

const FormatInfo = ({ availableFormats, selected, onClick }) => {
  return (
    <Block name="formats">
      <Elem name="info">You can export dataset version in one of the following formats:</Elem>
      <Elem name="list">
        {availableFormats.map((format) => (
          <Elem
            key={format.name}
            name="item"
            mod={{
              active: !format.disabled,
              selected: format.name === selected,
            }}
            onClick={!format.disabled ? () => onClick(format) : null}
          >
            <Elem name="name">
              {format.title}

              <Space size="small">
                {format.tags?.map?.((tag, index) => (
                  <Elem key={index} name="tag">
                    {tag}
                  </Elem>
                ))}
              </Space>
            </Elem>

            {format.description && <Elem name="description">{format.description}</Elem>}
          </Elem>
        ))}
      </Elem>
      <Elem name="feedback">
        Can't find an export format?
        <br />
        Please let us know in{" "}
        <a className="no-go" href="https://slack.labelstud.io/?source=product-export" target="_blank" rel="noreferrer">
          Slack
        </a>{" "}
        or submit an issue to the{" "}
        <a
          className="no-go"
          href="https://github.com/HumanSignal/label-studio-converter/issues"
          target="_blank"
          rel="noreferrer"
        >
          Repository
        </a>
      </Elem>
    </Block>
  );
};

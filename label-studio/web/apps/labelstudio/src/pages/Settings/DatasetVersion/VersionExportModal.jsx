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
      const response = await api.callApi("exportDatasetVersion", {
        params: {
          pk: project.id,
          versionId: version.id,
          ...params,
        },
      });

      if (response.ok) {
        const blob = await response.blob();
        downloadFile(blob, response.headers.get("filename"));
        onHide(); // Close modal after successful export
      } else {
        api.handleError(response);
      }
    } catch (error) {
      console.error('Export error:', error);
      api.handleError(error);
    }

    setDownloading(false);
    setDownloadingMessage(false);
    clearTimeout(message);
  };

  useEffect(() => {
    if (visible && project?.id) {
      api
        .callApi("exportFormats", {
          params: {
            pk: project.id,
          },
        })
        .then((formats) => {
          setAvailableFormats(formats);
          setCurrentFormat(formats[0]?.name || "JSON");
        })
        .catch((error) => {
          console.error('Failed to load export formats:', error);
        });
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
          <Input type="hidden" name="exportType" value={currentFormat} />
        </Form>

        <Elem name="footer">
          <Space style={{ width: "100%" }} spread>
            <Elem name="recent">{/* Previous exports could go here */}</Elem>
            <Elem name="actions">
              <Space>
                {downloadingMessage && "Files are being prepared. It might take some time."}
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

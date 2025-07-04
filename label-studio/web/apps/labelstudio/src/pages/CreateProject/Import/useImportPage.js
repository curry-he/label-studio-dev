import React, { useCallback } from "react";
import { useAPI } from "../../../providers/ApiProvider";
import { unique } from "../../../utils/helpers";
import { importFiles } from "./utils";

const DEFAULT_COLUMN = "$undefined$";

export const useImportPage = (project, sample) => {
  const [uploading, setUploadingStatus] = React.useState(false);
  const [fileIds, setFileIds] = React.useState([]);
  const [_columns, _setColumns] = React.useState([]);
  const addColumns = (cols) => _setColumns((current) => unique(current.concat(cols)));
  // undefined - no csv added, all good, keep moving
  // choose - csv added, block modal until user chooses a way to hangle csv
  // tasks | ts — choice made, all good, this cannot be undone
  const [csvHandling, setCsvHandling] = React.useState(); // undefined | choose | tasks | ts
  const uploadDisabled = csvHandling === "choose";
  const api = useAPI();

  // don't use columns from csv if we'll not use it as csv
  const columns = ["choose", "ts"].includes(csvHandling) ? [DEFAULT_COLUMN] : _columns;

  const finishUpload = async () => {
    setUploadingStatus(true);
    const result = await api.callApi("reimportFiles", {
      params: {
        pk: project.id,
      },
      body: {
        file_upload_ids: fileIds,
        files_as_tasks_list: csvHandling === "tasks",
      },
    });

    if (result && result.reimport) {
      // Poll for reimport status
      const poll = async () => {
        const status = await api.callApi("reimportStatus", {
          params: {
            pk: project.id,
            reimport_pk: result.reimport,
          },
        });

        if (status.status === 'completed') {
          setUploadingStatus(false);
          return status;
        } else if (status.status === 'failed') {
          setUploadingStatus(false);
          throw new Error(status.error);
        } else {
          setTimeout(poll, 1000);
        }
      };
      return await poll();
    } else {
      setUploadingStatus(false);
      return result;
    }
  };

  const uploadSample = useCallback(
    async (sample, onStart, onFinish) => {
      onStart?.();
      const url = sample.url;
      const body = new URLSearchParams({ url });
      await importFiles({
        files: [{ name: url }],
        body,
        project,
      });
      onFinish?.();
    },
    [project],
  );

  const pageProps = {
    onWaiting: setUploadingStatus,
    // onDisableSubmit: onDisableSubmit,
    highlightCsvHandling: uploadDisabled,
    addColumns,
    csvHandling,
    setCsvHandling,
    onFileListUpdate: setFileIds,
    dontCommitToProject: true,
  };

  return { columns, uploading, uploadDisabled, finishUpload, fileIds, pageProps, uploadSample };
};

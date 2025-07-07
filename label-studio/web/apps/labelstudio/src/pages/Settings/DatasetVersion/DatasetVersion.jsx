import { useState, useEffect, useContext } from "react";
import { Button, Space } from "../../../components";
import { Block, Elem } from "../../../utils/bem";
import { ApiContext } from "../../../providers/ApiProvider";
import { useProject } from "../../../providers/ProjectProvider";
import styles from "./DatasetVersion.scss";
import { modal } from "../../../components/Modal/Modal";
import { ImportPage } from "../../CreateProject/Import/Import";
import { useImportPage } from "../../CreateProject/Import/useImportPage";

// Available preprocessing options
const PREPROCESSING_OPTIONS = {
  "auto_orient": {
    name: "Auto-Orient",
    description: "Automatically adjusts image orientation based on EXIF data.",
    params: []
  },
  "resize": {
    name: "Resize",
    description: "Resize the image to a specific size.",
    params: [
      { name: "width", type: "number", default: 640, label: "Width" },
      { name: "height", type: "number", default: 640, label: "Height" },
      {
        name: "mode",
        type: "select",
        default: "stretch_to",
        label: "Mode",
        options: [
          { value: "stretch_to", label: "Stretch to" },
          { value: "fit_within", label: "Fit within" },
          { value: "fill_center_crop", label: "Fill (with center crop) in" },
          { value: "fit_black_edges", label: "Fit (black edges) in" },
          { value: "fit_white_edges", label: "Fit (white edges) in" },
          { value: "fit_reflect_edges", label: "Fit (reflect edges) in" },
        ]
      }
    ]
  }
};

// Available augmentation options
const AUGMENTATION_OPTIONS = {
  "flip": {
    name: "Flip",
    description: "Flip the image horizontally or vertically.",
    params: [
      { name: "direction", type: "select", default: "horizontal", options: ["horizontal", "vertical"], label: "Direction" }
    ]
  },
  "rotate": {
    name: "Rotate",
    description: "Rotate the image by a certain angle.",
    params: [
      { name: "angle", type: "number", default: 90, label: "Angle (degrees)" }
    ]
  }
};

// Component for adding preprocessing steps
const AddPreprocessingStepModal = ({ onCancel, onAdd }) => {
  const [selectedStep, setSelectedStep] = useState("");
  const [params, setParams] = useState({});

  const handleStepSelect = (stepKey) => {
    setSelectedStep(stepKey);
    const defaultParams = {};
    PREPROCESSING_OPTIONS[stepKey]?.params?.forEach(param => {
      defaultParams[param.name] = param.default;
    });
    setParams(defaultParams);
  };

  const handleParamChange = (paramName, value) => {
    setParams(prev => ({
      ...prev,
      [paramName]: value
    }));
  };

  const handleAdd = () => {
    if (selectedStep) {
      onAdd({
        type: selectedStep,
        name: PREPROCESSING_OPTIONS[selectedStep].name,
        params: params
      });
    }
  };

  return (
    <div className={styles['step-modal']}>
      <h3>Add Preprocessing Step</h3>
      <div className={styles['step-modal__field']}>
        <label>Select Preprocessing Step:</label>
        <select
          value={selectedStep}
          onChange={(e) => handleStepSelect(e.target.value)}
        >
          <option value="">Choose a preprocessing step...</option>
          {Object.entries(PREPROCESSING_OPTIONS).map(([key, option]) => (
            <option key={key} value={key}>{option.name}</option>
          ))}
        </select>
      </div>

      {selectedStep && (
        <div>
          <p className={styles['step-modal__description']}>
            {PREPROCESSING_OPTIONS[selectedStep].description}
          </p>

          {PREPROCESSING_OPTIONS[selectedStep].params?.map(param => (
            <div key={param.name} className={styles['step-modal__field']}>
              <label>{param.label}:</label>
              {param.type === 'number' ? (
                <input
                  type="number"
                  value={params[param.name] || param.default}
                  onChange={(e) => handleParamChange(param.name, parseInt(e.target.value))}
                />
              ) : param.type === 'select' ? (
                <select
                  value={params[param.name] || param.default}
                  onChange={(e) => handleParamChange(param.name, e.target.value)}
                >
                  {param.options.map(option => (
                    typeof option === 'string' ? (
                      <option key={option} value={option}>{option}</option>
                    ) : (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    )
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={params[param.name] || param.default}
                  onChange={(e) => handleParamChange(param.name, e.target.value)}
                />
              )}
            </div>
          ))}
        </div>
      )}

      <div className={styles['step-modal__actions']}>
        <Button onClick={onCancel}>Cancel</Button>
        <Button onClick={handleAdd} primary disabled={!selectedStep}>
          Add Step
        </Button>
      </div>
    </div>
  );
};

// Component for adding augmentation steps
const AddAugmentationStepModal = ({ onCancel, onAdd }) => {
  const [selectedStep, setSelectedStep] = useState("");
  const [params, setParams] = useState({});

  const handleStepSelect = (stepKey) => {
    setSelectedStep(stepKey);
    const defaultParams = {};
    AUGMENTATION_OPTIONS[stepKey]?.params?.forEach(param => {
      defaultParams[param.name] = param.default;
    });
    setParams(defaultParams);
  };

  const handleParamChange = (paramName, value) => {
    setParams(prev => ({
      ...prev,
      [paramName]: value
    }));
  };

  const handleAdd = () => {
    if (selectedStep) {
      onAdd({
        type: selectedStep,
        name: AUGMENTATION_OPTIONS[selectedStep].name,
        params: params
      });
    }
  };

  return (
    <div className={styles['step-modal']}>
      <h3>Add Augmentation Step</h3>
      <div className={styles['step-modal__field']}>
        <label>Select Augmentation Step:</label>
        <select
          value={selectedStep}
          onChange={(e) => handleStepSelect(e.target.value)}
        >
          <option value="">Choose an augmentation step...</option>
          {Object.entries(AUGMENTATION_OPTIONS).map(([key, option]) => (
            <option key={key} value={key}>{option.name}</option>
          ))}
        </select>
      </div>

      {selectedStep && (
        <div>
          <p className={styles['step-modal__description']}>
            {AUGMENTATION_OPTIONS[selectedStep].description}
          </p>

          {AUGMENTATION_OPTIONS[selectedStep].params?.map(param => (
            <div key={param.name} className={styles['step-modal__field']}>
              <label>{param.label}:</label>
              {param.type === 'number' ? (
                <input
                  type="number"
                  value={params[param.name] || param.default}
                  onChange={(e) => handleParamChange(param.name, parseInt(e.target.value))}
                />
              ) : param.type === 'select' ? (
                <select
                  value={params[param.name] || param.default}
                  onChange={(e) => handleParamChange(param.name, e.target.value)}
                >
                  {param.options.map(option => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={params[param.name] || param.default}
                  onChange={(e) => handleParamChange(param.name, e.target.value)}
                />
              )}
            </div>
          ))}
        </div>
      )}

      <div className={styles['step-modal__actions']}>
        <Button onClick={onCancel}>Cancel</Button>
        <Button onClick={handleAdd} primary disabled={!selectedStep}>
          Add Step
        </Button>
      </div>
    </div>
  );
};

// Component for displaying preprocessing steps
const PreprocessingStepCard = ({ step, onEdit, onRemove }) => {
  return (
    <div className={styles['preprocessing-step-card']}>
      <div className={styles['preprocessing-step-card__header']}>
        <div>
          <h4 className={styles['preprocessing-step-card__title']}>
            {step.name}
          </h4>
          {step.params && Object.keys(step.params).length > 0 && (
            <div className={styles['preprocessing-step-card__params']}>
              {Object.entries(step.params).map(([key, value]) => (
                <span key={key}>
                  {key}: <strong>{value}</strong>
                </span>
              ))}
            </div>
          )}
        </div>
        <div className={styles['preprocessing-step-card__actions']}>
          <Button size="small" onClick={() => onEdit(step)}>Edit</Button>
          <Button size="small" onClick={() => onRemove(step)} style={{ color: '#dc3545' }}>×</Button>
        </div>
      </div>
    </div>
  );
};

const AddMoreImagesModal = ({ onCancel, onFinish, pageProps, uploading, project }) => {
  const [sample, setSample] = useState(null);

  if (!project) {
    return (
      <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '20px',
          borderBottom: '1px solid #e0e0e0'
        }}>
          <h2>Import Data</h2>
          <Space>
            <Button onClick={onCancel}>Cancel</Button>
          </Space>
        </div>
        <div style={{ flex: 1, padding: '20px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div>Project not available. Please try again.</div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '20px',
        borderBottom: '1px solid #e0e0e0'
      }}>
        <h2>Import Data</h2>
        <Space>
          <Button onClick={onCancel}>Cancel</Button>
          <Button look="primary" onClick={onFinish} waiting={uploading}>
            Import
          </Button>
        </Space>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
        <ImportPage
          {...pageProps}
          project={project}
          sample={sample}
          onSampleDatasetSelect={setSample}
          projectConfigured={Object.keys(project?.parsed_label_config ?? {}).length > 0}
          show={true}
        />
      </div>
    </div>
  );
};

const RebalanceModal = ({ onCancel, onSave, project, versions }) => {
  const [trainPercent, setTrainPercent] = useState(70);
  const [validPercent, setValidPercent] = useState(20);
  const [testPercent, setTestPercent] = useState(10);

  const totalTasks = project?.task_number || 0;

  const calculateCounts = (train, valid, test) => {
    const total = train + valid + test;
    if (total === 0) return { trainCount: 0, validCount: 0, testCount: 0 };

    const trainCount = Math.round(totalTasks * (train / total));
    const validCount = Math.round(totalTasks * (valid / total));
    const testCount = totalTasks - trainCount - validCount; // Ensure total matches

    return { trainCount, validCount, testCount };
  };

  const { trainCount, validCount, testCount } = calculateCounts(trainPercent, validPercent, testPercent);

  const handleSave = () => {
    onSave({
      train_percent: trainPercent / 100,
      validation_percent: validPercent / 100,
      test_percent: testPercent / 100,
    });
  };

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '20px',
        borderBottom: '1px solid #e0e0e0'
      }}>
        <h2>Rebalance Train/Test Split</h2>
        <Space>
          <Button onClick={onCancel}>Cancel</Button>
          <Button look="primary" onClick={handleSave}>
            Save
          </Button>
        </Space>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
        <p>You can update your dataset's <b>train/test split</b> here.</p>
        <p>Note: changing your test set will invalidate model performance comparisons with previously generated versions.</p>

        <div style={{ marginBottom: '20px' }}>
          <label>Train Set ({trainCount} Images):</label>
          <input
            type="number"
            value={trainPercent}
            onChange={(e) => setTrainPercent(parseInt(e.target.value) || 0)}
            style={{ width: '100%', padding: '8px', boxSizing: 'border-box' }}
          />
        </div>
        <div style={{ marginBottom: '20px' }}>
          <label>Valid Set ({validCount} Images):</label>
          <input
            type="number"
            value={validPercent}
            onChange={(e) => setValidPercent(parseInt(e.target.value) || 0)}
            style={{ width: '100%', padding: '8px', boxSizing: 'border-box' }}
          />
        </div>
        <div style={{ marginBottom: '20px' }}>
          <label>Test Set ({testCount} Images):</label>
          <input
            type="number"
            value={testPercent}
            onChange={(e) => setTestPercent(parseInt(e.target.value) || 0)}
            style={{ width: '100%', padding: '8px', boxSizing: 'border-box' }}
          />
        </div>
        <p>Total Percentage: {trainPercent + validPercent + testPercent}%</p>
        {trainPercent + validPercent + testPercent !== 100 && (
          <p style={{ color: 'red' }}>Warning: Percentages do not add up to 100%.</p>
        )}
      </div>
    </div>
  );
};

const CreateVersionForm = ({ onVersionCreated, versions, project, onUploadFinished }) => {
  const api = useContext(ApiContext);
  const [activeStep, setActiveStep] = useState(1);
  const [versionName, setVersionName] = useState("");
  const [importError, setImportError] = useState(null);
  const [rebalanceLoading, setRebalanceLoading] = useState(false);
  const [splitConfig, setSplitConfig] = useState({ train: 70, valid: 20, test: 10 });
  const [currentProject, setCurrentProject] = useState(project);
  const [preprocessingSteps, setPreprocessingSteps] = useState([]);
  const [augmentationSteps, setAugmentationSteps] = useState([]);

  // 当project prop更新时，同步更新本地状态
  useEffect(() => {
    if (project) {
      setCurrentProject(project);
    }
  }, [project]);

  // 确保 project 存在再初始化 useImportPage
  const { finishUpload, pageProps, uploading } = useImportPage(currentProject || {});

  const handleCreateVersion = async () => {
    if (!currentProject?.id) return;
    const projectId = currentProject.id;

    const body = {
      project: projectId,
      name: versionName,
      version: `v${versions.length + 1}`,
      preprocessing_config: preprocessingSteps,
      augmentation_config: augmentationSteps,
      split_config: {
        train_percent: splitConfig.train / 100,
        validation_percent: splitConfig.valid / 100,
        test_percent: splitConfig.test / 100,
      },
    };
    const newVersion = await api.callApi("createDatasetVersion", {
      params: { pk: projectId },
      body,
    });
    if (newVersion) {
      onVersionCreated(newVersion);
    }
  };

  // Preprocessing step handlers
  const handleAddPreprocessingStep = (step) => {
    setPreprocessingSteps(prev => [...prev, { ...step, id: Date.now() }]);
  };

  const handleEditPreprocessingStep = (step) => {
    // TODO: Implement edit functionality
    console.log("Edit preprocessing step:", step);
  };

  const handleRemovePreprocessingStep = (stepToRemove) => {
    setPreprocessingSteps(prev => prev.filter(step => step.id !== stepToRemove.id));
  };

  const handleAddPreprocessingStepClick = () => {
    const modalRef = modal({
      title: "Add Preprocessing Step",
      body: (
        <AddPreprocessingStepModal
          onCancel={() => modalRef.close()}
          onAdd={(step) => {
            handleAddPreprocessingStep(step);
            modalRef.close();
          }}
        />
      ),
      style: { width: "600px", height: "auto", maxWidth: "90vw" },
      bare: true,
    });
  };

  // Augmentation step handlers
  const handleAddAugmentationStep = (step) => {
    setAugmentationSteps(prev => [...prev, { ...step, id: Date.now() }]);
  };

  const handleEditAugmentationStep = (step) => {
    // TODO: Implement edit functionality
    console.log("Edit augmentation step:", step);
  };

  const handleRemoveAugmentationStep = (stepToRemove) => {
    setAugmentationSteps(prev => prev.filter(step => step.id !== stepToRemove.id));
  };

  const handleAddAugmentationStepClick = () => {
    const modalRef = modal({
      title: "Add Augmentation Step",
      body: (
        <AddAugmentationStepModal
          onCancel={() => modalRef.close()}
          onAdd={(step) => {
            handleAddAugmentationStep(step);
            modalRef.close();
          }}
        />
      ),
      style: { width: "600px", height: "auto", maxWidth: "90vw" },
      bare: true,
    });
  };

  const handleAddMoreImages = () => {
    console.log("handleAddMoreImages called", { currentProject, pageProps, uploading });

    if (!currentProject?.id) {
      console.error("No project available for import");
      setImportError("Project not available");
      return;
    }

    setImportError(null);

    const modalRef = modal({
      title: "Import Data",
      body: (
        <AddMoreImagesModal
          onCancel={() => {
            console.log("Modal cancelled");
            setImportError(null);
            modalRef.close();
          }}
          onFinish={async () => {
            try {
              console.log("Starting upload finish");
              setImportError(null);
              const result = await finishUpload();
              console.log("Upload finished", result);
              if (result) {
                modalRef.close();
                // 刷新父组件的项目数据
                await onUploadFinished(currentProject.id);
              }
            } catch (error) {
              console.error("Failed to upload files:", error);
              setImportError("Failed to upload files: " + error.message);
            }
          }}
          pageProps={pageProps}
          uploading={uploading}
          project={currentProject}
        />
      ),
      style: { width: "90vw", height: "90vh", maxWidth: "1200px" },
      bare: true,
    });
  };

  const handleRebalanceClick = () => {
    const modalRef = modal({
      title: "Rebalance Train/Test Split",
      body: (
        <RebalanceModal
          onCancel={() => modalRef.close()}
          onSave={(percentages) => {
            setSplitConfig({
              train: percentages.train_percent * 100,
              valid: percentages.validation_percent * 100,
              test: percentages.test_percent * 100,
            });
            modalRef.close();
          }}
          project={currentProject}
          versions={versions}
        />
      ),
      style: { width: "600px", height: "auto", maxWidth: "90vw" },
      bare: true,
    });
  };

  const steps = [
    { id: 1, title: "Source Images" },
    { id: 2, title: "Train/Test Split" },
    { id: 3, title: "Preprocessing" },
    { id: 4, title: "Augmentation" },
    { id: 5, title: "Create" },
  ];

  return (
    <Block name="settings-wrapper">
      <h2>Create New Version</h2>
      <p>Prepare your images and data for training by compiling them into a version. Experiment with different configurations to achieve better training results.</p>
      
      {steps.map(step => (
        <div key={step.id} className={`${styles.step} ${activeStep === step.id ? styles.active : ''}`}>
          <div className={styles['step__number-wrapper']} onClick={() => setActiveStep(step.id)}>
            <div className={styles['step__number']}>{step.id}</div>
          </div>
          <div className={styles['step__body']}>
            <div className={styles['step__title']} onClick={() => setActiveStep(step.id)}>{step.title}</div>
            {activeStep === step.id && (
              <div className={styles['step__content']}>
                {step.id === 1 && (
                  <>
                    {currentProject ? (
                      <>
                        <div>Images: {currentProject.task_number}</div>
                        <div>Classes: {Object.keys(currentProject.parsed_label_config || {}).length}</div>
                        <div>Unannotated: {currentProject.task_number - (currentProject.num_tasks_with_annotations || 0)}</div>
                      </>
                    ) : (
                      <div>No project data available</div>
                    )}
                    {importError && (
                      <div style={{
                        marginTop: 10,
                        padding: 10,
                        backgroundColor: '#fee',
                        border: '1px solid #fcc',
                        borderRadius: 4,
                        color: '#c00'
                      }}>
                        {importError}
                      </div>
                    )}
                    <div style={{ marginTop: 20, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div>
                        <Button onClick={handleAddMoreImages} disabled={!currentProject?.id}>
                           + Add More Images
                        </Button>
                      </div>
                      <Button onClick={() => setActiveStep(2)} primary>Continue</Button>
                    </div>
                  </>
                )}
                {step.id === 2 && (() => {
                  const trainCount = Math.round(currentProject.task_number * (splitConfig.train / 100));
                  const validCount = Math.round(currentProject.task_number * (splitConfig.valid / 100));
                  const testCount = currentProject.task_number - trainCount - validCount;

                  return (
                    <>
                      <div className={styles['split-controls']}>
                        <div className={`${styles['split-box']} ${styles.train}`}>
                          <h4 className={styles['split-box__title']}>
                            <span>TRAIN SET</span>
                            <span className={styles['split-box__percent']}>{splitConfig.train}%</span>
                          </h4>
                          <p className={styles['split-box__images']}>{trainCount} Images</p>
                        </div>
                        <div className={`${styles['split-box']} ${styles.valid}`}>
                          <h4 className={styles['split-box__title']}>
                            <span>VALID SET</span>
                            <span className={styles['split-box__percent']}>{splitConfig.valid}%</span>
                          </h4>
                          <p className={styles['split-box__images']}>{validCount} Images</p>
                        </div>
                        <div className={`${styles['split-box']} ${styles.test}`}>
                          <h4 className={styles['split-box__title']}>
                            <span>TEST SET</span>
                            <span className={styles['split-box__percent']}>{splitConfig.test}%</span>
                          </h4>
                          <p className={styles['split-box__images']}>{testCount} Images</p>
                        </div>
                      </div>
                      <div style={{ marginTop: 20, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <Button onClick={handleRebalanceClick} waiting={rebalanceLoading} disabled={!project?.id || rebalanceLoading}>
                          Rebalance
                        </Button>
                        <Button onClick={() => setActiveStep(3)} primary>Continue</Button>
                      </div>
                    </>
                  );
                })()}
                {step.id === 3 && (
                  <>
                    <div style={{ marginBottom: '20px' }}>
                      <p style={{ color: '#666', fontSize: '14px', marginBottom: '15px' }}>
                        What are preprocessing steps?
                      </p>
                      <p style={{ marginBottom: '20px' }}>
                        Decrease training time and increase performance by applying image transformations to all images in this dataset.
                      </p>
                    </div>

                    {/* Display existing preprocessing steps */}
                    {preprocessingSteps.length > 0 && (
                      <div style={{ marginBottom: '20px' }}>
                        {preprocessingSteps.map((step) => (
                          <PreprocessingStepCard
                            key={step.id}
                            step={step}
                            onEdit={handleEditPreprocessingStep}
                            onRemove={handleRemovePreprocessingStep}
                          />
                        ))}
                      </div>
                    )}

                    {/* Add preprocessing step button */}
                    <Button onClick={handleAddPreprocessingStepClick}>
                      + Add Preprocessing Step
                    </Button>

                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                      <Button onClick={() => setActiveStep(4)} primary>Continue</Button>
                    </div>
                  </>
                )}
                {step.id === 4 && (
                  <>
                    <div style={{ marginBottom: '20px' }}>
                      <p style={{ marginBottom: '20px' }}>
                        Create new training examples for your model to learn from by generating augmented versions of each image in your training set.
                      </p>
                    </div>

                    {/* Display existing augmentation steps */}
                    {augmentationSteps.length > 0 && (
                      <div style={{ marginBottom: '20px' }}>
                        {augmentationSteps.map((step) => (
                          <PreprocessingStepCard
                            key={step.id}
                            step={step}
                            onEdit={handleEditAugmentationStep}
                            onRemove={handleRemoveAugmentationStep}
                          />
                        ))}
                      </div>
                    )}

                    {/* Add augmentation step button */}
                    <Button onClick={handleAddAugmentationStepClick}>
                      + Add Augmentation Step
                    </Button>

                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                      <Button onClick={() => setActiveStep(5)} primary>Continue</Button>
                    </div>
                  </>
                )}
                {step.id === 5 && (
                  <>
                    <p>Provide a name for this version to help you identify it later.</p>
                    <input
                      type="text"
                      placeholder="e.g. experiment-with-new-settings"
                      value={versionName}
                      onChange={(e) => setVersionName(e.target.value)}
                      style={{ width: '100%', padding: '8px', boxSizing: 'border-box', marginBottom: '20px' }}
                    />
                    <Button primary onClick={handleCreateVersion} disabled={!versionName}>Create Version</Button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      ))}
    </Block>
  );
};

const VersionDetails = ({ version, project }) => {
  const { split_stats, preprocessing_config, augmentation_config, task_count } = version;
  const totalImages = task_count;

  const trainPercentage = totalImages > 0 ? Math.round((split_stats.train / totalImages) * 100) : 0;
  const validPercentage = totalImages > 0 ? Math.round((split_stats.valid / totalImages) * 100) : 0;
  const testPercentage = totalImages > 0 ? Math.round((split_stats.test / totalImages) * 100) : 0;

  const handleViewAll = (split = null) => {
    let url = `/projects/${project.id}/data?project=${project.id}&version=${version.id}`;
    if (split) {
      url += `&split=${split}`;
    }
    window.location.href = url;
  };

  return (
    <Block name="version-details" className={styles['version-details']}>
      <div className={styles.section}>
        <div className={styles['section-header']}>
          <h3>Dataset Details</h3>
        </div>

        <hr />

        <div className={styles['detail-item']}>
          <h3>Image Number</h3>
          <div className={styles['total-images']}>
            <span className={styles['total-images__number']}>{totalImages}</span>
            <span className={styles['total-images__label']}>Total Images</span>
          </div>
          <a href="#" onClick={(e) => { e.preventDefault(); handleViewAll(); }} className={styles['view-all-link']} style={{ marginLeft: 'auto' }}>
            View All Images &rarr;
          </a>
        </div>

        <hr />

        <div className={styles['detail-item']}>
          <h3>Dataset Split</h3>
          <div className={styles['split-container']}>
            <div className={`${styles['split-card']} ${styles.train}`} onClick={() => handleViewAll('train')}>
              <div className={styles['split-card__header']}>
                <h4>TRAIN SET</h4>
                <span>{trainPercentage}%</span>
              </div>
              <p>{split_stats.train} Images</p>
            </div>
            <div className={`${styles['split-card']} ${styles.valid}`} onClick={() => handleViewAll('valid')}>
              <div className={styles['split-card__header']}>
                <h4>VALID SET</h4>
                <span>{validPercentage}%</span>
              </div>
              <p>{split_stats.valid} Images</p>
            </div>
            <div className={`${styles['split-card']} ${styles.test}`} onClick={() => handleViewAll('test')}>
              <div className={styles['split-card__header']}>
                <h4>TEST SET</h4>
                <span>{testPercentage}%</span>
              </div>
              <p>{split_stats.test} Images</p>
            </div>
          </div>
        </div>

        <hr />

        <div className={styles['detail-item']}>
          <h3>Preprocessing</h3>
          <div>
            {preprocessing_config && Array.isArray(preprocessing_config) && preprocessing_config.length > 0 ? (
              preprocessing_config.map((step, index) => (
                <p key={index}>
                  <strong>{step.name}:</strong>{' '}
                  {step.type === 'auto_orient' && 'Applied'}
                  {step.type === 'resize' && (
                    <span>
                      {
                        {
                          stretch_to: "Stretched to",
                          fit_within: "Fit within",
                          fill_center_crop: "Filled and cropped to",
                          fit_black_edges: "Fit with black edges to",
                          fit_white_edges: "Fit with white edges to",
                          fit_reflect_edges: "Fit with reflected edges to",
                        }[step.params.mode] || 'Resized to'
                      }
                      {' '}{step.params.width}x{step.params.height}
                    </span>
                  )}
                </p>
              ))
            ) : (
              <p>No preprocessing steps were applied.</p>
            )}
          </div>
        </div>

        <hr />

        <div className={styles['detail-item']}>
          <h3>Augmentations</h3>
          <div>
            {augmentation_config && Array.isArray(augmentation_config) && augmentation_config.length > 0 ? (
              augmentation_config.map((step, index) => (
                <p key={index}>
                  <strong>{step.name}:</strong>{' '}
                  {step.type === 'flip' && `Direction: ${step.params.direction}`}
                  {step.type === 'rotate' && `Angle: ${step.params.angle}°`}
                </p>
              ))
            ) : (
              <p>No augmentations were applied.</p>
            )}
          </div>
        </div>
      </div>
    </Block>
  );
};

export const DatasetVersion = () => {
  const api = useContext(ApiContext);
  const { project: contextProject } = useProject();
  const [versions, setVersions] = useState([]);
  const [project, setProject] = useState(null);
  const [selectedVersion, setSelectedVersion] = useState(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isLoadingProject, setIsLoadingProject] = useState(false);

  const fetchProjectAndVersions = async (projectId) => {
    const proj = await api.callApi("project", { params: { pk: projectId } });
    if (proj) {
      setProject(proj);
      const response = await api.callApi("datasetVersions", {
        params: { pk: projectId },
      });
      if (response && Array.isArray(response)) {
        const sortedVersions = response.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        setVersions(sortedVersions);
        if (!selectedVersion && sortedVersions.length > 0) {
          setSelectedVersion(sortedVersions[0]);
        }
      }
    }
  };

  useEffect(() => {
    if (contextProject?.id) {
      fetchProjectAndVersions(contextProject.id);
    }
  }, [contextProject?.id]);

  const handleCreateNewVersion = async () => {
    if (!contextProject?.id) {
      return;
    }

    setIsCreating(true);
    setSelectedVersion(null);
    setIsLoadingProject(true);
    try {
      const proj = await api.callApi("project", { params: { pk: contextProject.id } });
      if (proj) {
        setProject(proj);
      } else {
        setIsCreating(false);
      }
    } catch (e) {
      console.error("Failed to load project data", e);
      setIsCreating(false);
    } finally {
      setIsLoadingProject(false);
    }
  };

  const handleSelectVersion = (version) => {
    setSelectedVersion(version);
    setIsCreating(false);
  };

  const handleVersionCreated = async (newVersion) => {
    setIsCreating(false);
    await fetchProjectAndVersions(contextProject.id); // Refresh the list of versions using contextProject.id
    if (newVersion) {
      setSelectedVersion(newVersion); // Select the newly created version
    }
  };

  const handleRenameVersion = async (version) => {
    const newName = prompt("Enter new version name:", version.name);
    if (newName && newName !== version.name) {
      try {
        await api.callApi("updateDatasetVersion", {
          params: { pk: project.id, versionId: version.id },
          body: { name: newName },
        });
        await fetchProjectAndVersions(contextProject.id);
      } catch (error) {
        console.error("Failed to rename version:", error);
      }
    }
  };

  const handleDeleteVersion = async (version) => {
    if (window.confirm(`Are you sure you want to delete version "${version.name}"? This cannot be undone.`)) {
      try {
        await api.callApi("deleteDatasetVersion", {
          params: { pk: project.id, versionId: version.id },
        });
        setSelectedVersion(null);
        await fetchProjectAndVersions(contextProject.id);
      } catch (error) {
        console.error("Failed to delete version:", error);
      }
    }
  };

  return (
    <Block name="dataset-version">
      <Elem name="header">
        <h1>Versions</h1>
        <Button
          onClick={handleCreateNewVersion}
          primary
          disabled={isLoadingProject}
        >
          {isLoadingProject ? "Loading..." : "Create New Version"}
        </Button>
      </Elem>
      <Elem name="container">
        <Elem name="versions-list">
          {versions.map((version) => (
            <div
              key={version.id}
              className={`${styles['version-item']} ${selectedVersion?.id === version.id ? styles.selected : ''} ${project?.active_version === version.id ? styles.activeVersion : ''}`}
              onClick={() => handleSelectVersion(version)}
            >
              <div style={{ flex: 1, cursor: 'pointer' }}>
                <div className={styles['version-item__name']}>{version.name}</div>
              </div>
              <div className={styles['version-item__actions']}>
                <Space size="small">
                  <Button size="small" onClick={(e) => { e.stopPropagation(); handleRenameVersion(version); }}>
                    Rename
                  </Button>
                  <Button size="small" look="danger" onClick={(e) => { e.stopPropagation(); handleDeleteVersion(version); }}>
                    Delete
                  </Button>
                </Space>
              </div>
            </div>
          ))}
        </Elem>
        <Elem name="version-content">
          {isCreating ?
            (project ? (
              <CreateVersionForm
                onVersionCreated={handleVersionCreated}
                versions={versions}
                project={project}
                onUploadFinished={fetchProjectAndVersions}
              />
            ) : (
              <div>Loading project data...</div>
            )) :
            (selectedVersion ? (
              <VersionDetails
                version={selectedVersion}
                project={project}
              />
            ) : (
              <div>Select a version to see details or create a new one.</div>
            ))}
        </Elem>
      </Elem>
    </Block>
  );
};

DatasetVersion.menuItem = "DataSet Versions";
DatasetVersion.path = "/versions";

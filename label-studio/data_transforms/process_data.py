import os
import json
from PIL import Image
from preprocessing import apply_preprocessing, transform_annotations as transform_annotations_preprocessing
from augmentation import apply_augmentation

def main():
    input_dir = os.environ.get('PFS_INPUT', '/pfs/in')
    output_dir = os.environ.get('PFS_OUTPUT', '/pfs/out')

    config_path = os.path.join(input_dir, 'config.json')
    with open(config_path, 'r') as f:
        config = json.load(f)

    data_dir = os.path.join(input_dir, 'data')
    for filename in os.listdir(data_dir):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            image_path = os.path.join(data_dir, filename)
            
            # This is a placeholder for annotation loading.
            # In a real scenario, you'd load annotations for each image.
            annotations = [] 

            with Image.open(image_path) as img:
                original_width, original_height = img.size

                # Apply preprocessing
                processed_img, pp_params = apply_preprocessing(img, config.get('preprocessing'))
                
                # Apply augmentation (example for a 'train' subset)
                # In a real pipeline, you'd need a way to determine the subset for each image.
                is_train_sample = True 
                if is_train_sample:
                    processed_img, _, aug_params = apply_augmentation(processed_img, annotations, config.get('augmentation'))
                else:
                    aug_params = {}

                # Save the processed image
                output_image_path = os.path.join(output_dir, filename)
                processed_img.save(output_image_path)

                # Transform annotations and save them
                transform_params = {**pp_params, **aug_params}
                new_annotations = transform_annotations_preprocessing(annotations, transform_params, original_width, original_height)
                
                output_annotation_path = os.path.join(output_dir, os.path.splitext(filename)[0] + '.json')
                with open(output_annotation_path, 'w') as f:
                    json.dump(new_annotations, f)

if __name__ == "__main__":
    main()

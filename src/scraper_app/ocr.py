from PIL import Image, ImageEnhance, ImageFilter
import pytesseract
import logging
from pathlib import Path
from typing import TypedDict, Union
from datetime import datetime

class OCRResult(TypedDict):
    text: str
    char_count: int
    word_count: int
    path: str

def ocr_image(img_path: Path | str, enhancement: bool = True, fast_processing: bool = False) -> OCRResult:
    """Perform OCR on an image file.
    
    Args:
        img_path (Path | str): Path to the image file
        enhancement (bool, optional): Whether to apply contrast enhancement and sharpening. Defaults to True.
        fast_processing (bool, optional): If True, skips resizing for larger images (>1000x1000). Defaults to False.
        
    Returns:
        OCRResult: Dictionary containing:
            - text (str): Extracted text from the image
            - char_count (int): Number of characters extracted
            - word_count (int): Number of words extracted
            - path (str): Input image path as string
            
    Raises:
        ValueError: If the image is empty or corrupted
    """
    try:
        logging.debug(f"Starting OCR processing for {img_path}")
        img = Image.open(str(img_path)).convert('RGB')
        
        # Log image format and mode
        logging.debug(f"Image format: {img.format}, mode: {img.mode}")

        # Check if image is empty or corrupted
        if img.getbbox() is None:
            error_msg = f"Image appears to be empty or corrupted: {img_path}"
            logging.error(error_msg)
            raise ValueError(error_msg)

        # Convert to grayscale
        gray = img.convert('L')
        logging.debug(f"Image converted to grayscale: {gray.size}")

        # Resize (2x upscale if image is small)
        if not fast_processing or (gray.width < 1000 and gray.height < 1000):
            if gray.width < 300 or gray.height < 300:
                old_size = gray.size
                gray = gray.resize((gray.width * 2, gray.height * 2), Image.LANCZOS)
                logging.debug(f"Image upscaled from {old_size} to {gray.size}")
        else:
            logging.debug(f"Skipping resize for large image ({gray.width}x{gray.height}) due to fast_processing=True")

        # Improve contrast and sharpness if enhancement is enabled
        if enhancement:
            gray = ImageEnhance.Contrast(gray).enhance(2.0)  # Increase contrast
            gray = gray.filter(ImageFilter.SHARPEN)         # Apply sharpen filter
            logging.debug("Applied contrast enhancement and sharpening")
        else:
            logging.debug("Skipping image enhancement")

        # Run OCR
        text = pytesseract.image_to_string(gray)
        text_length = len(text)
        word_count = len(text.split())
        logging.info(f"OCR completed: extracted {text_length} characters, {word_count} words")
        
        if text_length == 0:
            logging.warning(f"No text extracted from {img_path}")
        
        return {
            "text": text,
            "char_count": text_length,
            "word_count": word_count,
            "path": str(img_path)
        }
    except FileNotFoundError as e:
        logging.error(f"Image file not found: {img_path} - {str(e)}")
        return {
            "text": "",
            "char_count": 0,
            "word_count": 0,
            "path": str(img_path)
        }
    except pytesseract.TesseractError as e:
        logging.error(f"Tesseract OCR error for {img_path}: {str(e)}")
        return {
            "text": "",
            "char_count": 0,
            "word_count": 0,
            "path": str(img_path)
        }
    except ValueError as e:
        logging.error(str(e))
        return {
            "text": "",
            "char_count": 0,
            "word_count": 0,
            "path": str(img_path)
        }
    except Exception as e:
        logging.error(f"Unexpected error during OCR for {img_path}: {str(e)}")
        return {
            "text": "",
            "char_count": 0,
            "word_count": 0,
            "path": str(img_path)
        }

def generate_ocr_summary(images: list) -> dict:
    """Generate a summary of OCR results from a list of images.
    
    Args:
        images: List of image dictionaries containing OCR results
        
    Returns:
        dict: Summary of OCR results including:
            - total_ocr_text: Combined text from all images
            - total_ocr_text_length: Total character count
            - total_ocr_word_count: Total word count
            - image_count: Number of images processed
            - successful_ocr_count: Number of images with successful OCR
            - timestamp: Processing timestamp
    """
    logging.info(f"Generating OCR summary for {len(images)} images")
    
    total_text = ""
    total_char_count = 0
    total_word_count = 0
    successful_ocr_count = 0
    
    image_summaries = []
    
    for img in images:
        # Check if this image has OCR text
        ocr_text = img.get('ocr_text', '')
        if not ocr_text and 'text' in img:  # Fallback to 'text' field if 'ocr_text' not present
            ocr_text = img.get('text', '')
            
        if ocr_text:
            successful_ocr_count += 1
            total_text += ocr_text + "\n\n"
            
            # Calculate metrics
            char_count = len(ocr_text)
            word_count = len(ocr_text.split())
            total_char_count += char_count
            total_word_count += word_count
            
            # Add individual image summary
            image_summaries.append({
                'image_url': img.get('image_url', ''),
                'image_path': img.get('image_path', str(img.get('path', ''))),
                'ocr_text_length': char_count,
                'ocr_text_word_count': word_count,
                'ocr_success': True
            })
        else:
            # Add failed OCR attempt
            image_summaries.append({
                'image_url': img.get('image_url', ''),
                'image_path': img.get('image_path', str(img.get('path', ''))),
                'ocr_text_length': 0,
                'ocr_text_word_count': 0,
                'ocr_success': False
            })
    
    # Create summary dictionary
    summary = {
        'total_ocr_text': total_text.strip(),
        'total_ocr_text_length': total_char_count,
        'total_ocr_word_count': total_word_count,
        'image_count': len(images),
        'successful_ocr_count': successful_ocr_count,
        'success_rate': (successful_ocr_count / len(images)) * 100 if images else 0,
        'timestamp': {
            'created': datetime.now().isoformat()
        },
        'image_summaries': image_summaries
    }
    
    logging.info(f"OCR summary generated: {successful_ocr_count}/{len(images)} images with text, {total_char_count} chars, {total_word_count} words")
    return summary

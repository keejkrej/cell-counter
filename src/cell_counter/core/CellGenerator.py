"""
Core cell generator functionality for cell-counter.
"""

import cv2
import numpy as np
from skimage import img_as_ubyte
from nd2reader import ND2Reader
from typing import List, Tuple
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_GRID_SIZE = 20
AREA_STD_DEVIATIONS = 2
GAUSSIAN_BLUR_SIZE = (5, 5)

class CellGenerator:
    """
    A class for generating and processing cell data from images.
    
    This class handles the loading and processing of pattern and cell images from ND2 files,
    providing methods to extract and analyze cell regions.
    
    Attributes:
        patterns_path (str): Path to the patterns ND2 file
        cells_path (str): Path to the cell ND2 file
        patterns_reader (ND2Reader): Reader for patterns ND2 file
        cells_reader (ND2Reader): Reader for cells ND2 file
        patterns_metadata (Dict[str, int]): Metadata for patterns file
        cells_metadata (Dict[str, int]): Metadata for cells file
        n_views (int): Number of views in the files
        n_frames (int): Number of frames in the cells file
        current_view (int): Current view index
        current_frame (int): Current frame index
        patterns (Optional[np.ndarray]): Current patterns image
        n_patterns (int): Number of detected patterns
        contours (Optional[List[np.ndarray]]): Pattern contours
        bounding_boxes (Optional[List[Tuple[int, int, int, int]]]): Pattern bounding boxes
        centers (Optional[List[Tuple[int, int]]]): Pattern centers
        frame_nuclei (Optional[np.ndarray]): Current nuclei frame
        frame_cyto (Optional[np.ndarray]): Current cytoplasm frame
    """

    # =====================================================================
    # Constructor and Initialization
    # =====================================================================

    def __init__(self, patterns_path: str, cells_path: str) -> None:
        """
        Initialize the CellGenerator with paths to pattern and cell images.
        
        Args:
            patterns_path (str): Path to the patterns ND2 file
            cells_path (str): Path to the cell ND2 file containing nuclei and cytoplasm channels
            
        Raises:
            ValueError: If initialization fails or files are invalid
        """
        self.patterns_path = str(Path(patterns_path).resolve())
        self.cells_path = str(Path(cells_path).resolve())
        
        try:
            self._init_patterns()
            self._init_cells()
            self._validate_files()
            logger.info(f"Successfully initialized CellGenerator with patterns: {self.patterns_path} and cells: {self.cells_path}")
        except Exception as e:
            self.close_files()
            logger.error(f"Error initializing ND2 readers: {e}")
            raise ValueError(f"Error initializing ND2 readers: {e}")
        
        self._init_memory()

    def _init_patterns(self) -> None:
        """Initialize the patterns reader and metadata."""
        try:
            self.patterns_reader = ND2Reader(self.patterns_path)
            self.patterns_metadata = {
                'channels': self.patterns_reader.sizes.get('c', 0),
                'frames': self.patterns_reader.sizes.get('t', 0),
                'views': self.patterns_reader.sizes.get('v', 0),
            }
            logger.debug(f"Patterns metadata: {self.patterns_metadata}")
        except Exception as e:
            logger.error(f"Error initializing patterns reader: {e}")
            raise

    def _init_cells(self) -> None:
        """Initialize the cells reader and metadata."""
        try:
            self.cells_reader = ND2Reader(self.cells_path)
            self.cells_metadata = {
                'channels': self.cells_reader.sizes.get('c', 0),
                'frames': self.cells_reader.sizes.get('t', 0),
                'views': self.cells_reader.sizes.get('v', 0),
            }
            logger.debug(f"Cells metadata: {self.cells_metadata}")
        except Exception as e:
            logger.error(f"Error initializing cells reader: {e}")
            raise

    def _validate_files(self) -> None:
        """
        Validate the ND2 files meet the required specifications.
        
        Raises:
            ValueError: If files don't meet the required specifications
        """
        if self.patterns_metadata['channels'] != 1:
            raise ValueError("Patterns ND2 file must contain exactly 1 channel")
        if self.cells_metadata['channels'] != 2:
            raise ValueError("Cells ND2 file must contain exactly 2 channels (nuclei and cytoplasm)")
        if self.patterns_metadata['frames'] != 1:
            raise ValueError("Patterns ND2 file must contain exactly 1 frame")
        if self.patterns_metadata['views'] != self.cells_metadata['views']:
            raise ValueError("Patterns and cells ND2 files must contain the same number of views")
        
        self.n_views = self.cells_metadata['views']
        self.n_frames = self.cells_metadata['frames']
        logger.info(f"Validated files: {self.n_views} views, {self.n_frames} frames")

    def _init_memory(self) -> None:
        """Initialize memory variables to default values."""
        self.current_view = 0
        self.current_frame = 0
        self.patterns = None
        self.n_patterns = 0
        self.contours = None
        self.bounding_boxes = None
        self.centers = None
        self.frame_nuclei = None
        self.frame_cyto = None
        logger.debug("Initialized memory variables")

    # =====================================================================
    # Private Methods
    # =====================================================================

    def _find_contours(self, image: np.ndarray) -> List[np.ndarray]:
        """
        Find contours in an image using thresholding and contour detection.
        
        Args:
            image (np.ndarray): Input image to find contours in
            
        Returns:
            List[np.ndarray]: List of detected contours
            
        Raises:
            ValueError: If image is None or empty
        """
        if image is None or image.size == 0:
            raise ValueError("Image must not be None or empty")
            
        # Apply Gaussian blur to reduce noise
        blur = cv2.GaussianBlur(image, GAUSSIAN_BLUR_SIZE, 0)
        
        # Apply Otsu's thresholding
        _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        # Find contours
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        logger.debug(f"Found {len(contours)} contours in image")
        
        return contours

    def _refine_contours(self, contours: List[np.ndarray]) -> List[Tuple[int, int, np.ndarray, Tuple[int, int, int, int]]]:
        """
        Refine contours by filtering based on area statistics and sorting.
        
        Args:
            contours (List[np.ndarray]): List of contours to refine
            
        Returns:
            List[Tuple[int, int, np.ndarray, Tuple[int, int, int, int]]]: List of refined contour data
        """
        if contours is None or len(contours) == 0:
            raise ValueError("Contours must not be None or empty")
        
        # First pass: collect all areas to calculate statistics
        areas = [cv2.contourArea(contour) for contour in contours]
        
        # Calculate mean and standard deviation of areas
        mean_area = np.mean(areas)
        std_area = np.std(areas)
        
        # Calculate centers and store with contours and bounding boxes
        contour_data = []
        for contour in contours:
            area = cv2.contourArea(contour)
            x, y, w, h = cv2.boundingRect(contour)
            
            # Skip if area deviates too much from mean
            if abs(area - mean_area) > AREA_STD_DEVIATIONS * std_area:
                continue
                
            # Calculate center
            center_x = x + w // 2
            center_y = y + h // 2
            
            contour_data.append((center_y, center_x, contour, (x, y, w, h)))
        
        # Sort by y (row) first, then x within each row
        contour_data.sort(key=lambda x: (x[0], x[1]))
        logger.debug(f"Refined {len(contours)} contours to {len(contour_data)} valid contours")
        return contour_data
    
    def _extract_region(self, frame: np.ndarray, pattern_idx: int) -> np.ndarray:
        """
        Extract a region from a frame based on pattern index.
        
        Args:
            frame (np.ndarray): Frame to extract region from
            pattern_idx (int): Index of the pattern to extract
            
        Returns:
            np.ndarray: Extracted region
            
        Raises:
            ValueError: If frame is None, pattern index is invalid, or extraction fails
        """
        if frame is None:
            raise ValueError("Frame not provided")
        if pattern_idx >= self.n_patterns or pattern_idx < 0:
            raise ValueError(f"Pattern index {pattern_idx} out of range (0-{self.n_patterns-1})")
        if self.bounding_boxes is None:
            raise ValueError("No bounding boxes provided")
        
        try:
            x, y, w, h = self.bounding_boxes[pattern_idx]
            region = frame[y:y+h, x:x+w]
            return region
        except Exception as e:
            logger.error(f"Error extracting region: {e}")
            raise ValueError(f"Error extracting region: {e}")

    # =====================================================================
    # Public Methods
    # =====================================================================

    def close_files(self) -> None:
        """Safely close all ND2 readers."""
        if hasattr(self, 'patterns_reader'):
            self.patterns_reader.close()
        if hasattr(self, 'cells_reader'):
            self.cells_reader.close()
        logger.debug("Closed all ND2 readers")

    def load_view(self, view_idx: int) -> None:
        """
        Load a specific view from the ND2 files.
        
        Args:
            view_idx (int): Index of the view to load
            
        Raises:
            ValueError: If view index is invalid
        """
        if view_idx >= self.n_views or view_idx < 0:
            raise ValueError(f"View index {view_idx} out of range (0-{self.n_views-1})")
        self.current_view = view_idx
        logger.info(f"Loaded view {view_idx}")

    def load_patterns(self) -> None:
        """
        Load the patterns from ND2 file.
        
        Raises:
            ValueError: If loading fails
        """
        try:
            self.patterns = img_as_ubyte(self.patterns_reader.get_frame_2D(c=0, t=0, v=self.current_view))
            logger.debug(f"Loaded patterns for view {self.current_view}")
        except Exception as e:
            logger.error(f"Error loading patterns: {e}")
            raise ValueError(f"Error loading patterns: {e}")
    
    def load_nuclei(self, frame_idx: int) -> None:
        """
        Load nuclei frame from ND2 file.
        
        Args:
            frame_idx (int): Index of the frame to load
            
        Raises:
            ValueError: If frame index is invalid or loading fails
        """
        if frame_idx >= self.n_frames:
            raise ValueError(f"Frame index {frame_idx} out of range (0-{self.n_frames-1})")
        try:
            self.frame_nuclei = img_as_ubyte(self.cells_reader.get_frame_2D(c=0, t=frame_idx, v=self.current_view))
            logger.debug(f"Loaded nuclei frame {frame_idx} for view {self.current_view}")
        except Exception as e:
            logger.error(f"Error loading nuclei: {e}")
            raise ValueError(f"Error loading nuclei: {e}")
    
    def load_cyto(self, frame_idx: int) -> None:
        """
        Load cytoplasm frame from ND2 file.
        
        Args:
            frame_idx (int): Index of the frame to load
            
        Raises:
            ValueError: If frame index is invalid or loading fails
        """
        if frame_idx >= self.n_frames:
            raise ValueError(f"Frame index {frame_idx} out of range (0-{self.n_frames-1})")
        try:
            self.frame_cyto = img_as_ubyte(self.cells_reader.get_frame_2D(c=1, t=frame_idx, v=self.current_view))
            logger.debug(f"Loaded cytoplasm frame {frame_idx} for view {self.current_view}")
        except Exception as e:
            logger.error(f"Error loading cyto: {e}")
            raise ValueError(f"Error loading cyto: {e}")
        
    def process_patterns(self) -> None:
        """
        Process pattern image to extract contours and their bounding boxes.
        
        Raises:
            ValueError: If patterns haven't been loaded
        """
        if self.patterns is None:
            raise ValueError("Patterns must be loaded before processing")
            
        contours = self._find_contours(self.patterns)
        contour_data = self._refine_contours(contours)
        self.contours = [x[2] for x in contour_data]
        self.bounding_boxes = [x[3] for x in contour_data]
        self.centers = [x[0:2] for x in contour_data]
        self.n_patterns = len(self.contours)
        logger.info(f"Processed {self.n_patterns} patterns")

    def extract_nuclei(self, pattern_idx: int) -> np.ndarray:
        """
        Extract nuclei region for a specific pattern.
        
        Args:
            pattern_idx (int): Index of the pattern to extract
            
        Returns:
            np.ndarray: Extracted nuclei region
            
        Raises:
            ValueError: If nuclei frame hasn't been loaded
        """
        if self.frame_nuclei is None:
            raise ValueError("Nuclei frame must be loaded before extraction")
        return self._extract_region(self.frame_nuclei, pattern_idx)
    
    def extract_cyto(self, pattern_idx: int) -> np.ndarray:
        """
        Extract cytoplasm region for a specific pattern.
        
        Args:
            pattern_idx (int): Index of the pattern to extract
            
        Returns:
            np.ndarray: Extracted cytoplasm region
            
        Raises:
            ValueError: If cytoplasm frame hasn't been loaded
        """
        if self.frame_cyto is None:
            raise ValueError("Cytoplasm frame must be loaded before extraction")
        return self._extract_region(self.frame_cyto, pattern_idx)

    def extract_pattern(self, pattern_idx: int) -> np.ndarray:
        """
        Extract pattern region.
        
        Args:
            pattern_idx (int): Index of the pattern to extract
            
        Returns:
            np.ndarray: Extracted pattern region
            
        Raises:
            ValueError: If patterns haven't been loaded
        """
        if self.patterns is None:
            raise ValueError("Patterns must be loaded before extraction")
        return self._extract_region(self.patterns, pattern_idx)

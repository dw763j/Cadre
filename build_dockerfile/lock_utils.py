import time
from loguru import logger

def create_rw_locks(manager):
    """Create shared read-write locks for build and clean operations."""
    return {
        'readers': manager.Value('i', 0),  # current active builds
        'writer_active': manager.Value('i', 0),  # whether cleaning operation is active
        'readers_lock': manager.Lock(),  # reader lock for builds
        'writer_lock': manager.Lock()   # writer lock for cleaning operation
    }


def acquire_read_lock(locks):
    """Acquire shared lock for build operation."""
    # Wait for current cleaning operation to finish
    if locks['writer_active'].value == 1:
        logger.debug("The build is waiting for the cleaning...")
    while locks['writer_active'].value == 1:
        time.sleep(1)

    # acount the active build
    locks['readers_lock'].acquire()
    locks['readers'].value += 1
    logger.debug(f"Gained reader lock for build, current builds: {locks['readers'].value}")
    locks['readers_lock'].release()


def release_read_lock(locks):
    """Release shared lock for build operation."""
    locks['readers_lock'].acquire()
    locks['readers'].value -= 1
    logger.debug(f"Released reader lock for build, current builds: {locks['readers'].value}")
    locks['readers_lock'].release()


def acquire_write_lock(locks):
    """Acquire exclusive lock for clean operation."""
    locks['writer_lock'].acquire()

    # Flag the cleaning operation is active
    locks['writer_active'].value = 1
    logger.debug("Cleaning operation gained the writter lock")

    # Wait for all builds to finish
    if locks['readers'].value > 0:
        logger.debug(f"The cleaning operation is waiting for {locks['readers'].value} builds...")
    while locks['readers'].value > 0:
        time.sleep(1)


def release_write_lock(locks):
    """Release exclusive lock for clean operation."""
    locks['writer_active'].value = 0
    locks['writer_lock'].release()
    logger.debug("Cleaning operation released the writter lock")
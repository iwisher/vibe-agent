"""Bounded LRU Cache for task queries.

FIXES: O(n²) file scanning in memory/manager.py
TARGET: <50ms query for 10K tasks with bounded memory
"""

import asyncio
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set


@dataclass
class CachedTask:
    """Cached task with metadata for LRU eviction."""
    task_id: str
    data: dict
    access_count: int = 0
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    size_bytes: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TaskMemoryCache:
    """
    Simple LRU cache for task queries.
    
    Limits:
    - 1000 entries max
    - 100MB memory max
    - 1 hour TTL
    
    Features:
    - O(1) get by task_id
    - O(1) query by status (indexed)
    - Thread-safe with asyncio.Lock
    """
    
    DEFAULT_MAX_ENTRIES = 1000
    DEFAULT_MAX_MEMORY_MB = 100.0
    DEFAULT_TTL_SECONDS = 3600  # 1 hour
    
    def __init__(
        self, 
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_memory_mb: float = DEFAULT_MAX_MEMORY_MB,
        ttl_seconds: int = DEFAULT_TTL_SECONDS
    ):
        self.max_entries = max_entries
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.ttl = timedelta(seconds=ttl_seconds)
        
        # OrderedDict maintains insertion order for LRU
        self._cache: OrderedDict[str, CachedTask] = OrderedDict()
        
        # Fast lookup indexes
        self._status_index: Dict[str, Set[str]] = {}
        self._tag_index: Dict[str, Set[str]] = {}
        
        self._lock = asyncio.Lock()
        self._current_memory = 0
        self._hits = 0
        self._misses = 0
        
    async def get(self, task_id: str) -> Optional[dict]:
        """Get task by ID (O(1))."""
        async with self._lock:
            if task_id not in self._cache:
                self._misses += 1
                return None
                
            cached = self._cache[task_id]
            
            # Check TTL
            if datetime.now(timezone.utc) - cached.created_at > self.ttl:
                self._evict(task_id)
                self._misses += 1
                return None
                
            # Update LRU order (move to end = most recent)
            self._cache.move_to_end(task_id)
            cached.access_count += 1
            cached.last_accessed = datetime.now(timezone.utc)
            
            self._hits += 1
            return cached.data
            
    async def set(self, task_id: str, data: dict) -> None:
        """Cache task data."""
        async with self._lock:
            # Calculate size before acquiring space
            size = len(json.dumps(data).encode('utf-8'))
            
            # Check if we need to evict
            await self._ensure_space(size)
            
            # Check if updating existing entry
            if task_id in self._cache:
                old_cached = self._cache[task_id]
                self._current_memory -= old_cached.size_bytes
                self._remove_from_indexes(task_id, old_cached.data)
            
            # Create cached entry
            cached = CachedTask(
                task_id=task_id,
                data=data,
                size_bytes=size
            )
            
            self._cache[task_id] = cached
            self._current_memory += size
            
            # Update indexes
            self._update_indexes(task_id, data)
            
    async def mset(self, tasks: List[dict], key_field: str = "id") -> int:
        """Cache multiple tasks at once."""
        count = 0
        for task in tasks:
            task_id = task.get(key_field)
            if task_id:
                await self.set(task_id, task)
                count += 1
        return count
            
    async def query(self, **filters) -> List[dict]:
        """Query cached tasks by filters (O(1) with index, O(n) fallback)."""
        async with self._lock:
            results = []
            now = datetime.now(timezone.utc)
            status_filter = filters.get('status')
            
            # Use index if querying by status
            if status_filter and status_filter in self._status_index:
                task_ids = self._status_index[status_filter].copy()
                for task_id in task_ids:
                    if task_id not in self._cache:
                        continue
                    cached = self._cache[task_id]
                    
                    # Check TTL
                    if now - cached.created_at > self.ttl:
                        self._evict(task_id)
                        continue
                        
                    if self._matches_filters(cached.data, filters):
                        cached.access_count += 1
                        cached.last_accessed = now
                        results.append(cached.data)
            else:
                # Full scan (fallback for unindexed queries)
                for task_id, cached in list(self._cache.items()):
                    # Check TTL
                    if now - cached.created_at > self.ttl:
                        self._evict(task_id)
                        continue
                        
                    if self._matches_filters(cached.data, filters):
                        cached.access_count += 1
                        cached.last_accessed = now
                        results.append(cached.data)
                        
            return results
            
    async def invalidate(self, task_id: str = None, pattern: str = None) -> int:
        """Invalidate cache entries.
        
        Args:
            task_id: Specific task ID to invalidate
            pattern: Pattern to match task IDs (substring match)
            
        Returns:
            Number of entries invalidated
        """
        async with self._lock:
            if task_id:
                if task_id in self._cache:
                    self._evict(task_id)
                    return 1
                return 0
            elif pattern:
                # Invalidate by pattern
                count = 0
                for task_id in list(self._cache.keys()):
                    if pattern in task_id:
                        self._evict(task_id)
                        count += 1
                return count
            else:
                # Clear all
                count = len(self._cache)
                self._cache.clear()
                self._current_memory = 0
                self._reset_indexes()
                return count
                
    async def invalidate_by_status(self, status: str) -> int:
        """Invalidate all tasks with given status."""
        async with self._lock:
            count = 0
            if status in self._status_index:
                for task_id in list(self._status_index[status]):
                    if task_id in self._cache:
                        self._evict(task_id)
                        count += 1
            return count
                
    async def _ensure_space(self, new_size: int) -> None:
        """Evict entries if needed to make room."""
        # Check memory limit
        while (self._current_memory + new_size > self.max_memory_bytes and 
               len(self._cache) > 0):
            self._evict_lru()
            
        # Check entry limit
        while len(self._cache) >= self.max_entries:
            self._evict_lru()
            
    def _evict_lru(self) -> None:
        """Evict least recently used entry."""
        if not self._cache:
            return
        # Pop from beginning (oldest)
        task_id, cached = self._cache.popitem(last=False)
        self._current_memory -= cached.size_bytes
        self._remove_from_indexes(task_id, cached.data)
        
    def _evict(self, task_id: str) -> None:
        """Evict specific entry."""
        if task_id in self._cache:
            cached = self._cache.pop(task_id)
            self._current_memory -= cached.size_bytes
            self._remove_from_indexes(task_id, cached.data)
            
    def _update_indexes(self, task_id: str, data: dict) -> None:
        """Update query indexes."""
        # Index by status
        status = data.get('status')
        if status:
            if status not in self._status_index:
                self._status_index[status] = set()
            self._status_index[status].add(task_id)
            
        # Index by tags
        tags = data.get('tags', [])
        if isinstance(tags, list):
            for tag in tags:
                if tag not in self._tag_index:
                    self._tag_index[tag] = set()
                self._tag_index[tag].add(task_id)
            
    def _remove_from_indexes(self, task_id: str, data: dict) -> None:
        """Remove from query indexes."""
        status = data.get('status')
        if status and status in self._status_index:
            self._status_index[status].discard(task_id)
            if not self._status_index[status]:
                del self._status_index[status]
                
        tags = data.get('tags', [])
        if isinstance(tags, list):
            for tag in tags:
                if tag in self._tag_index:
                    self._tag_index[tag].discard(task_id)
                    if not self._tag_index[tag]:
                        del self._tag_index[tag]
            
    def _reset_indexes(self) -> None:
        """Clear all indexes."""
        self._status_index.clear()
        self._tag_index.clear()
        
    def _matches_filters(self, data: dict, filters: dict) -> bool:
        """Check if data matches all filters."""
        for key, value in filters.items():
            if key == 'status' and data.get('status') != value:
                return False
            if key == 'task_type' and data.get('task_type') != value:
                return False
            if key == 'tag' and value not in data.get('tags', []):
                return False
            if key == 'priority' and data.get('priority') != value:
                return False
        return True
        
    def get_stats(self) -> dict:
        """Return cache statistics."""
        total_requests = self._hits + self._misses
        hit_rate = self._hits / total_requests if total_requests > 0 else 0.0
        
        return {
            'entries': len(self._cache),
            'memory_bytes': self._current_memory,
            'memory_mb': round(self._current_memory / (1024 * 1024), 2),
            'memory_limit_mb': round(self.max_memory_bytes / (1024 * 1024), 2),
            'entry_limit': self.max_entries,
            'hits': self._hits,
            'misses': self._misses,
            'hit_rate': round(hit_rate, 4),
            'status_indexes': len(self._status_index),
            'tag_indexes': len(self._tag_index),
        }
        
    async def get_all_task_ids(self) -> List[str]:
        """Get all cached task IDs."""
        async with self._lock:
            return list(self._cache.keys())
            
    async def exists(self, task_id: str) -> bool:
        """Check if task exists in cache (without updating LRU)."""
        async with self._lock:
            if task_id not in self._cache:
                return False
            cached = self._cache[task_id]
            # Check TTL
            if datetime.now(timezone.utc) - cached.created_at > self.ttl:
                return False
            return True


# Global cache instance (initialized on first use)
_cache_instance: Optional[TaskMemoryCache] = None


def get_cache() -> TaskMemoryCache:
    """Get or create global cache instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = TaskMemoryCache()
    return _cache_instance


def reset_cache() -> None:
    """Reset global cache instance (mainly for testing)."""
    global _cache_instance
    _cache_instance = None

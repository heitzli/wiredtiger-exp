"""Query optimizer component: currently passes algebra through unchanged. NOTE/TODO: planned rewrites include cross-product -> equi-join, and simpler duplicate-removing proj-on-proj collapsing."""
from .queryoptimizer_component import QueryOptimizer as QueryOptimizer

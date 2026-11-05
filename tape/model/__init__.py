"""The stack model and the reconciliation engine. Pure code, like compute.py.

core          ranges, entities, quantities, the config loader, estimates
conversions   market price -> physical or economic terms, with assumptions
gaps          model range against market range, persistence flags
scenarios     one input changed, pushed through documented links
ledger        forecast scoring and calibration
indicators    the leading-indicator test bench
"""

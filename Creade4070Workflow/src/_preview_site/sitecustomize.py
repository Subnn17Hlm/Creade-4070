"""Compatibility shim for langchain.callbacks"""
import sys
import langchain_core.callbacks

# Create langchain.callbacks module alias
sys.modules['langchain.callbacks'] = langchain_core.callbacks
sys.modules['langchain.callbacks.base'] = langchain_core.callbacks.base
sys.modules['langchain.callbacks.manager'] = langchain_core.callbacks.manager

# Also patch langchain module
import langchain
langchain.callbacks = langchain_core.callbacks

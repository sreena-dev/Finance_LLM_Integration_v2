"""Authentication and the platform's own tables.

Self-contained: the gateway attaches `deps.require_user` to its mode routers in
one line, and no mode router is edited to gain authentication. The only database
objects this package owns are `artha_users` and `artha_fs_messages`, both
created by `schema.py`.
"""

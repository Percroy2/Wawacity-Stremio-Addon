import asyncio
from wawacity.services.openlibrary import openlibrary_service

print(asyncio.run(openlibrary_service.get_cover_url("Le Chant de Pasiphaé")))

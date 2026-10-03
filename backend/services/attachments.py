import base64
import hashlib
from pathlib import Path
from backend.models.domain import uid
from backend.services.finance import require

class AttachmentStore:
    """Opaque names and a bounded local store; receipts are preserved, not parsed."""
    def __init__(self, root): self.root=Path(root)
    def save(self, file):
        try: content=base64.b64decode(file.data_base64,validate=True)
        except (ValueError,base64.binascii.Error):
            require(False,'File encoding is invalid')
        require(0<len(content)<=10_000_000,'Choose a file smaller than 10 MB')
        identifier=uid('file')
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/identifier
        path.write_bytes(content)
        return {'id':identifier,'filename':Path(file.filename.replace('\\','/')).name,
                'mime_type':file.mime_type,'byte_size':len(content),'sha256':hashlib.sha256(content).hexdigest()}
    def path(self, identifier):
        require(identifier.startswith('file_') and all(c.isalnum() or c=='_' for c in identifier),'Invalid attachment reference')
        path=(self.root/identifier).resolve()
        require(path.parent==self.root.resolve() and path.is_file(),'Attachment not found')
        return path

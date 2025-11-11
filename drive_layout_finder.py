import sys
import os
import io
import threading
import tempfile
from pathlib import Path
from functools import partial

from PyQt5 import QtWidgets, QtGui, QtCore
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials

from pdf2image import convert_from_bytes
from PIL import Image
import imagehash

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
DEFAULT_THRESHOLD = 5

def pil_image_to_qpixmap(pil_img, max_size=(200, 250)):
    pil_img.thumbnail(max_size)
    data = io.BytesIO()
    pil_img.save(data, format='PNG')
    qimg = QtGui.QImage.fromData(data.getvalue())
    return QtGui.QPixmap.fromImage(qimg)

class DriveLayoutFinder(QtWidgets.QTabWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Drive Layout Finder')
        self.resize(1000, 700)

        self.creds = None
        self.drive_service = None
        self.ref_image = None
        self.ref_hash = None
        self.temp_dir = Path(tempfile.mkdtemp(prefix='drive_layout_finder_'))
        self.matches = []

        self.build_ui()

    def build_ui(self):
        # Tabs
        self.ref_tab = QtWidgets.QWidget()
        self.scan_tab = QtWidgets.QWidget()
        self.results_tab = QtWidgets.QWidget()

        self.addTab(self.ref_tab, 'Reference PDF')
        self.addTab(self.scan_tab, 'Scan & Progress')
        self.addTab(self.results_tab, 'Results & Download')

        self.build_ref_tab()
        self.build_scan_tab()
        self.build_results_tab()

    def build_ref_tab(self):
        layout = QtWidgets.QVBoxLayout(self.ref_tab)
        self.sign_button = QtWidgets.QPushButton('Sign in to Google Drive')
        self.sign_button.clicked.connect(self.sign_in)
        layout.addWidget(self.sign_button)

        self.ref_button = QtWidgets.QPushButton('Choose Reference PDF')
        self.ref_button.clicked.connect(self.choose_reference)
        self.ref_button.setEnabled(False)
        layout.addWidget(self.ref_button)

        self.ref_preview = QtWidgets.QLabel('No reference selected')
        self.ref_preview.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.ref_preview)

        self.ref_hash_label = QtWidgets.QLabel('')
        layout.addWidget(self.ref_hash_label)

    def build_scan_tab(self):
        layout = QtWidgets.QVBoxLayout(self.scan_tab)
        self.threshold_label = QtWidgets.QLabel('Similarity threshold')
        layout.addWidget(self.threshold_label)

        self.threshold_spin = QtWidgets.QSpinBox()
        self.threshold_spin.setRange(0, 64)
        self.threshold_spin.setValue(DEFAULT_THRESHOLD)
        layout.addWidget(self.threshold_spin)

        self.scan_button = QtWidgets.QPushButton('Scan Google Drive')
        self.scan_button.setEnabled(False)
        self.scan_button.clicked.connect(self.scan_drive_threaded)
        layout.addWidget(self.scan_button)

        self.progress = QtWidgets.QProgressBar()
        layout.addWidget(self.progress)
        self.status = QtWidgets.QLabel('Not signed in')
        layout.addWidget(self.status)

    def build_results_tab(self):
        layout = QtWidgets.QVBoxLayout(self.results_tab)
        self.result_list = QtWidgets.QListWidget()
        self.result_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        layout.addWidget(self.result_list)

        self.download_button = QtWidgets.QPushButton('Download Selected')
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.download_selected)
        layout.addWidget(self.download_button)

    def sign_in(self):
        creds_path = Path('credentials.json')
        if not creds_path.exists():
            QtWidgets.QMessageBox.critical(self, 'Missing credentials.json',
                                           'Place your Google OAuth credentials.json next to the program and try again.')
            return
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            self.creds = flow.run_local_server(port=0)
            self.drive_service = build('drive', 'v3', credentials=self.creds)
            self.status.setText('Signed in to Google Drive')
            self.sign_button.setEnabled(False)
            self.ref_button.setEnabled(True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Sign-in error', str(e))

    def choose_reference(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Select reference PDF', str(Path.home()), 'PDF Files (*.pdf)')
        if not path:
            return
        try:
            with open(path, 'rb') as f:
                b = f.read()
            pages = convert_from_bytes(b, first_page=1, last_page=1)
            self.ref_image = pages[0]
            self.ref_hash = imagehash.phash(self.ref_image)
            pix = pil_image_to_qpixmap(self.ref_image, max_size=(400, 500))
            self.ref_preview.setPixmap(pix)
            self.ref_hash_label.setText(f'Reference hash: {self.ref_hash}')
            self.scan_button.setEnabled(True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, 'Reference error', f'Could not load reference PDF: {e}')

    def scan_drive_threaded(self):
        self.scan_button.setEnabled(False)
        threading.Thread(target=self.scan_drive).start()

    def scan_drive(self):
        self.matches = []
        self.result_list.clear()
        threshold = self.threshold_spin.value()
        self.progress.setRange(0, 0)
        self.status.setText('Scanning Drive...')
        try:
            page_token = None
            while True:
                res = self.drive_service.files().list(q="mimeType='application/pdf' and trashed=false",
                                                     spaces='drive',
                                                     fields='nextPageToken, files(id, name)',
                                                     pageToken=page_token,
                                                     pageSize=1000).execute()
                for f in res.get('files', []):
                    try:
                        request = self.drive_service.files().get_media(fileId=f['id'])
                        fh = io.BytesIO()
                        downloader = MediaIoBaseDownload(fh, request)
                        done = False
                        while not done:
                            status, done = downloader.next_chunk()
                        pages = convert_from_bytes(fh.getvalue(), first_page=1, last_page=1)
                        pil_img = pages[0]
                        diff = abs(self.ref_hash - imagehash.phash(pil_img))
                        if diff <= threshold:
                            preview_path = self.temp_dir / f"{f['id']}.png"
                            pil_img.save(preview_path)
                            rec = {'id': f['id'], 'name': f['name'], 'preview': str(preview_path), 'diff': int(diff)}
                            self.matches.append(rec)
                            QtCore.QMetaObject.invokeMethod(self, 'add_result_item', QtCore.Qt.QueuedConnection,
                                                            QtCore.Q_ARG(dict, rec))
                    except: pass
                page_token = res.get('nextPageToken', None)
                if not page_token:
                    break
        except Exception as e:
            QtCore.QMetaObject.invokeMethod(self, 'show_error', QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(str, str(e)))
        finally:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.scan_button.setEnabled(True)
            if self.matches:
                self.download_button.setEnabled(True)
            self.status.setText(f'Matches found: {len(self.matches)}')

    @QtCore.pyqtSlot(dict)
    def add_result_item(self, rec):
        item = QtWidgets.QListWidgetItem()
        widget = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout()
        pix = pil_image_to_qpixmap(Image.open(rec['preview']))
        thumb = QtWidgets.QLabel()
        thumb.setPixmap(pix)
        thumb.setFixedSize(200, 250)
        h.addWidget(thumb)
        v = QtWidgets.QVBoxLayout()
        name_label = QtWidgets.QLabel(rec['name'])
        diff_label = QtWidgets.QLabel(f'diff: {rec['diff']}')
        checkbox = QtWidgets.QCheckBox('Select')
        checkbox.setChecked(True)
        checkbox.file_id = rec['id']
        checkbox.filename = rec['name']
        v.addWidget(name_label)
        v.addWidget(diff_label)
        v.addWidget(checkbox)
        h.addLayout(v)
        widget.setLayout(h)
        item.setSizeHint(widget.sizeHint())
        self.result_list.addItem(item)
        self.result_list.setItemWidget(item, widget)

    @QtCore.pyqtSlot(str)
    def show_error(self, msg):
        QtWidgets.QMessageBox.critical(self, 'Error', msg)

    def download_selected(self):
        selected = []
        for i in range(self.result_list.count()):
            item = self.result_list.item(i)
            widget = self.result_list.itemWidget(item)
            chk = widget.findChild(QtWidgets.QCheckBox)
            if chk and chk.isChecked():
                selected.append((chk.file_id, chk.filename))
        if not selected:
            QtWidgets.QMessageBox.information(self, 'None selected', 'No files selected for download.')
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Choose download folder', str(Path.home()))
        if not folder:
            return
        dlg = QtWidgets.QProgressDialog('Downloading files...', 'Cancel', 0, len(selected), self)
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        dlg.show()
        for idx, (fid, fname) in enumerate(selected):
            try:
                request = self.drive_service.files().get_media(fileId=fid)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                out_path = Path(folder) / fname
                if out_path.exists():
                    base = out_path.stem
                    ext = out_path.suffix
                    k = 1
                    while (Path(folder) / f"{base}_{k}{ext}").exists():
                        k += 1
                    out_path = Path(folder) / f"{base}_{k}{ext}"
                with open(out_path, 'wb') as f:
                    f.write(fh.getvalue())
            except: pass
            dlg.setValue(idx+1)
            QtWidgets.QApplication.processEvents()
            if dlg.wasCanceled():
                break
        dlg.close()
        QtWidgets.QMessageBox.information(self, 'Done', 'Download finished.')

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    w = DriveLayoutFinder()
    w.show()
    sys.exit(app.exec_())

# -*- coding: utf-8 -*-

import sys
import os
import json
import re
import time
import io
import math
import wave
from pathlib import Path
import numpy as np
import scipy as sp
import scipy.io.wavfile
import simpleaudio
import requests
import dataclasses

from PySide6.QtCore import (
    Qt,
    QStringListModel,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QGroupBox,
    QSplitter,
    QTextEdit,
    QSizePolicy,
)
from PySide6.QtGui import (
    QColor,
)

from rs.core import (
    config,
    pipe as p,
    lang,
    util,
    chara_data,
    txt,
    srt,
    voicevox,
)
from rs.core.voicevox.data import SpeakerList
from rs.core.voicevox.api import (
    synthesis,
    audio_query,
    VOICEVOX_PORT,
    VOICEVOX_NEMO_PORT,
    SHAREVOX_PORT,
)
from rs.gui import (
    appearance,
    log,
)
from rs.gui.log import LogTextEdit
from rs_resolve.core import (
    get_currentframe,
    get_fps,
    track_name2index,
    get_track_names,
    get_item,
    Appender,
    LockOtherTrack,
)
from rs_resolve.gui import (
    get_resolve_window,
    activate_window,
)
from rs_resolve.tool.voicevox2wave.mora_adjuster import MoraAdjuster

APP_NAME = 'Voicevox2Wave'

@dataclasses.dataclass
class Engine:
    port: int = VOICEVOX_PORT
    name: str = 'VOICEVOX'
    speakers: SpeakerList = dataclasses.field(default_factory=SpeakerList)

    def get_speakers_file(self):
        return config.CONFIG_DIR.joinpath('%s_speakers.json' % self.name.lower().replace(' ', '_'))

    def load_speakers(self):
        _file = self.get_speakers_file()
        if _file.is_file():
            self.speakers.load(_file)

    def save_speakers(self):
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _file = self.get_speakers_file()
        self.speakers.save(_file)


@dataclasses.dataclass
class ConfigData(config.Data):
    out_dir: str = ''
    video_track: str = ''
    audio_track: str = ''
    engine_index: int = 0
    speaker_display_name: str = ''
    
    speed_scale: float = 1.0
    intonation_scale: float = 1.0
    volume_scale: float = 1.0
    pre_phoneme_len: float = 0.1
    post_phoneme_len: float = 0.1
    text: str = ''


class MainWindow(QMainWindow):
    def __init__(self, parent=None, fusion=None):
        super().__init__(parent)
        self.fusion = fusion
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowCloseButtonHint
            | Qt.WindowStaysOnTopHint
        )
        self.resize(800, 700)

        self.text_plus_dir_name: str = '__RS_TextPlus_FPS__'
        data_dir: Path = config.DATA_PATH.joinpath('app', 'VoiceDropper')
        self.text_plus_drb: Path = data_dir.joinpath(self.text_plus_dir_name + '.drb')

        # VOICEVOX Engines setup
        self.engine_list = [
            Engine(port=VOICEVOX_PORT, name='VOICEVOX'),
            Engine(port=VOICEVOX_NEMO_PORT, name='VOICEVOX Nemo'),
            Engine(port=SHAREVOX_PORT, name='SHAREVOX'),
        ]
        for engine in self.engine_list:
            engine.load_speakers()

        self.play_obj = None
        self.current_audio_query = None
        self.edit_target = None  # Holds context for timeline updating
        
        self.config_file: Path = config.CONFIG_DIR.joinpath('%s.json' % APP_NAME)
        
        self.init_ui()
        self.load_config()
        self.update_track()
        self.set_speaker_list()

    def init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # 1. Output Directory Area
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel('保存ディレクトリ:'))
        self.out_line_edit = QLineEdit(self)
        dir_layout.addWidget(self.out_line_edit)
        self.out_tool_btn = QToolButton(self)
        self.out_tool_btn.setText('...')
        self.out_tool_btn.clicked.connect(self.select_out_dir)
        dir_layout.addWidget(self.out_tool_btn)
        main_layout.addLayout(dir_layout)

        # 2. Tracks & VOICEVOX Settings Area
        settings_layout = QHBoxLayout()
        
        # Track Group
        track_group = QGroupBox('配置トラック', self)
        track_form = QFormLayout(track_group)
        self.video_combo = QComboBox(self)
        self.video_combo.setModel(QStringListModel())
        track_form.addRow('字幕 (Video):', self.video_combo)
        
        self.audio_combo = QComboBox(self)
        self.audio_combo.setModel(QStringListModel())
        track_form.addRow('音声 (Audio):', self.audio_combo)
        settings_layout.addWidget(track_group)

        # VOICEVOX Group
        voice_group = QGroupBox('VOICEVOX 設定', self)
        voice_form = QFormLayout(voice_group)
        
        engine_layout = QHBoxLayout()
        self.engine_combo = QComboBox(self)
        self.engine_combo.addItems([e.name for e in self.engine_list])
        self.engine_combo.currentIndexChanged.connect(self.on_engine_changed)
        engine_layout.addWidget(self.engine_combo)
        
        self.get_speakers_btn = QPushButton('話者取得', self)
        self.get_speakers_btn.clicked.connect(self.get_speakers)
        self.get_speakers_btn.setStyleSheet(appearance.other_stylesheet)
        engine_layout.addWidget(self.get_speakers_btn)
        voice_form.addRow('エンジン:', engine_layout)

        self.speaker_combo = QComboBox(self)
        voice_form.addRow('キャラクター:', self.speaker_combo)
        settings_layout.addWidget(voice_group)

        main_layout.addLayout(settings_layout)

        # 3. Parameters & Text Input Area (Splitter)
        splitter = QSplitter(Qt.Horizontal, self)
        
        # Parameter Adjusters Group
        param_group = QGroupBox('パラメータ設定', self)
        param_form = QFormLayout(param_group)
        
        self.speed_spin = QDoubleSpinBox(self)
        self.speed_spin.setRange(0.50, 2.00)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setValue(1.0)
        self.speed_spin.valueChanged.connect(self.on_param_changed)
        param_form.addRow('話速 (Speed):', self.speed_spin)

        self.intonation_spin = QDoubleSpinBox(self)
        self.intonation_spin.setRange(0.00, 2.00)
        self.intonation_spin.setSingleStep(0.1)
        self.intonation_spin.setValue(1.0)
        self.intonation_spin.valueChanged.connect(self.on_param_changed)
        param_form.addRow('抑揚 (Intonation):', self.intonation_spin)

        self.volume_spin = QDoubleSpinBox(self)
        self.volume_spin.setRange(0.00, 2.00)
        self.volume_spin.setSingleStep(0.1)
        self.volume_spin.setValue(1.0)
        self.volume_spin.valueChanged.connect(self.on_param_changed)
        param_form.addRow('音量 (Volume):', self.volume_spin)

        self.pre_len_spin = QDoubleSpinBox(self)
        self.pre_len_spin.setRange(0.00, 1.50)
        self.pre_len_spin.setSingleStep(0.1)
        self.pre_len_spin.setValue(0.1)
        self.pre_len_spin.valueChanged.connect(self.on_param_changed)
        param_form.addRow('開始無音 (sec):', self.pre_len_spin)

        self.post_len_spin = QDoubleSpinBox(self)
        self.post_len_spin.setRange(0.00, 1.50)
        self.post_len_spin.setSingleStep(0.1)
        self.post_len_spin.setValue(0.1)
        self.post_len_spin.valueChanged.connect(self.on_param_changed)
        param_form.addRow('終了無音 (sec):', self.post_len_spin)

        splitter.addWidget(param_group)

        # Text Input Group
        text_group = QGroupBox('セリフテキスト', self)
        text_layout = QVBoxLayout(text_group)
        self.text_edit = QPlainTextEdit(self)
        text_layout.addWidget(self.text_edit)
        
        self.query_btn = QPushButton('テキストから調声データを取得', self)
        self.query_btn.setStyleSheet(appearance.other_stylesheet)
        self.query_btn.clicked.connect(self.load_query_from_text)
        text_layout.addWidget(self.query_btn)
        
        splitter.addWidget(text_group)
        main_layout.addWidget(splitter)

        # 4. Mora Detail Adjustment Area (YMM4-like)
        mora_group = QGroupBox('詳細調声 (モーラ単位)', self)
        mora_layout = QVBoxLayout(mora_group)
        self.mora_adjuster = MoraAdjuster(self)
        self.mora_adjuster.changed.connect(self.on_mora_data_changed)
        mora_layout.addWidget(self.mora_adjuster)
        main_layout.addWidget(mora_group)

        # 5. Log Output Area
        log_group = QGroupBox('ログ', self)
        log_layout = QVBoxLayout(log_group)
        self.log_text = LogTextEdit(self)
        self.log_text.setFixedHeight(80)
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group)

        # 6. Action Button Area
        btn_layout = QHBoxLayout()
        
        self.read_btn = QPushButton('タイムラインから読込', self)
        self.read_btn.setMinimumHeight(40)
        self.read_btn.setStyleSheet(appearance.other_stylesheet)
        self.read_btn.clicked.connect(self.read_from_timeline)
        btn_layout.addWidget(self.read_btn)

        self.preview_btn = QPushButton('再生プレビュー', self)
        self.preview_btn.setMinimumHeight(40)
        self.preview_btn.setStyleSheet(appearance.other_stylesheet)
        self.preview_btn.clicked.connect(self.preview)
        btn_layout.addWidget(self.preview_btn)

        self.apply_btn = QPushButton('タイムラインへ適用 (追加/更新)', self)
        self.apply_btn.setMinimumHeight(40)
        self.apply_btn.setStyleSheet(appearance.ex_stylesheet)
        self.apply_btn.clicked.connect(self.apply_to_timeline)
        btn_layout.addWidget(self.apply_btn)

        self.close_btn = QPushButton('閉じる', self)
        self.close_btn.setMinimumHeight(40)
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)

        main_layout.addLayout(btn_layout)

    def select_out_dir(self):
        path = QFileDialog.getExistingDirectory(self, '保存先フォルダの選択', self.out_line_edit.text())
        if path:
            self.out_line_edit.setText(path)

    def add_log(self, text: str, color: QColor = log.TEXT_COLOR):
        self.log_text.log(text, color)

    def add_error(self, text: str):
        self.log_text.log(text, log.ERROR_COLOR)

    def on_engine_changed(self):
        self.set_speaker_list()

    def set_speaker_list(self):
        self.speaker_combo.clear()
        idx = self.engine_combo.currentIndex()
        if idx >= 0 and idx < len(self.engine_list):
            speaker_list = self.engine_list[idx].speakers
            self.speaker_combo.addItems(speaker_list.get_display_name_list())

    def get_speakers(self):
        self.log_text.clear()
        self.add_log('話者リストを取得中...')
        idx = self.engine_combo.currentIndex()
        engine = self.engine_list[idx]
        try:
            engine.speakers.set_from_voicevox(port=engine.port)
            self.set_speaker_list()
            engine.save_speakers()
            self.add_log('話者リストの取得に成功しました。')
        except Exception as e:
            self.add_error(f'取得失敗: {e}')

    def update_track(self):
        resolve = self.fusion.GetResolve()
        projectManager = resolve.GetProjectManager()
        project = projectManager.GetCurrentProject()
        if project is None:
            return
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            return

        v_names = get_track_names(timeline, 'video')
        a_names = get_track_names(timeline, 'audio')

        self.video_combo.model().setStringList(v_names)
        self.audio_combo.model().setStringList(a_names)

    def get_selected_speaker_id(self):
        idx = self.engine_combo.currentIndex()
        engine = self.engine_list[idx]
        display_name = self.speaker_combo.currentText()
        return engine.speakers.get_id_from_display_name(display_name)

    def load_query_from_text(self):
        self.log_text.clear()
        text = self.text_edit.toPlainText().strip()
        if not text:
            self.add_error('セリフを入力してください。')
            return

        speaker_id = self.get_selected_speaker_id()
        if speaker_id is None:
            self.add_error('有効なキャラクターを選択してください。')
            return

        idx = self.engine_combo.currentIndex()
        engine = self.engine_list[idx]

        self.add_log('VOICEVOXからクエリを取得中...')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            query = audio_query(text, speaker_id, max_retry=5, port=engine.port)
            self.current_audio_query = query
            
            # Apply current basic sliders to new query
            self.current_audio_query['speedScale'] = self.speed_spin.value()
            self.current_audio_query['intonationScale'] = self.intonation_spin.value()
            self.current_audio_query['volumeScale'] = self.volume_spin.value()
            self.current_audio_query['prePhonemeLength'] = self.pre_len_spin.value()
            self.current_audio_query['postPhonemeLength'] = self.post_len_spin.value()

            # Load into mora adjuster UI
            self.mora_adjuster.set_query(self.current_audio_query)
            self.add_log('音声クエリの取得が完了しました。調声が可能です。')
        except Exception as e:
            self.add_error(f'クエリ取得失敗: {e}')
        finally:
            QApplication.restoreOverrideCursor()

    def on_param_changed(self):
        if self.current_audio_query is None:
            return
        self.current_audio_query['speedScale'] = self.speed_spin.value()
        self.current_audio_query['intonationScale'] = self.intonation_spin.value()
        self.current_audio_query['volumeScale'] = self.volume_spin.value()
        self.current_audio_query['prePhonemeLength'] = self.pre_len_spin.value()
        self.current_audio_query['postPhonemeLength'] = self.post_len_spin.value()

    def on_mora_data_changed(self):
        # Triggered when sliders in MoraAdjuster are moved.
        # Self-updating of current_audio_query happens in MoraAdjuster.
        pass

    def stop(self):
        if self.play_obj is not None:
            self.play_obj.stop()
            self.play_obj = None

    def preview(self):
        self.log_text.clear()
        if self.current_audio_query is None:
            self.add_error('先に「調声データ取得」を実行してください。')
            return

        speaker_id = self.get_selected_speaker_id()
        idx = self.engine_combo.currentIndex()
        engine = self.engine_list[idx]

        self.add_log('合成中 (プレビュー)...')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.stop()
        try:
            audio_bytes = synthesis(speaker_id, self.current_audio_query, max_retry=5, port=engine.port)
            fs, data = sp.io.wavfile.read(io.BytesIO(audio_bytes))
            self.add_log('再生中...')
            self.play_obj = simpleaudio.play_buffer(data, 1, 2, self.current_audio_query.get('outputSamplingRate', 24000))
        except Exception as e:
            self.add_error(f'合成失敗: {e}')
        finally:
            QApplication.restoreOverrideCursor()

    def make_dropper_folder(self, project):
        media_pool = project.GetMediaPool()
        root_folder = media_pool.GetRootFolder()
        current_folder = media_pool.GetCurrentFolder()
        
        dropper_folder = None
        for folder in root_folder.GetSubFolderList():
            if folder.GetName() == 'VoiceDropper':
                dropper_folder = folder
                break
        if dropper_folder is None:
            dropper_folder = media_pool.AddSubFolder(root_folder, 'VoiceDropper')

        text_plus_folder = None
        for folder in dropper_folder.GetSubFolderList():
            if folder.GetName() == self.text_plus_dir_name:
                text_plus_folder = folder
                break
        if text_plus_folder is None:
            media_pool.SetCurrentFolder(dropper_folder)
            media_pool.ImportFolderFromFile(str(self.text_plus_drb))
            media_pool.SetCurrentFolder(current_folder)

    def write_lab_file(self, lab_path: Path, query: dict):
        phonemes = []
        pre_len = query.get('prePhonemeLength', 0.0)
        post_len = query.get('postPhonemeLength', 0.0)
        
        if pre_len > 0.0:
            phonemes.append({'sign': 'pau', 'length': pre_len})
            
        for phrase in query.get('accent_phrases', []):
            for mora in phrase.get('moras', []):
                if mora.get('consonant') is not None:
                    phonemes.append({
                        'sign': mora['consonant'],
                        'length': mora['consonant_length']
                    })
                phonemes.append({
                    'sign': mora['vowel'],
                    'length': mora['vowel_length']
                })
            if phrase.get('pause_mora') is not None:
                phonemes.append({
                    'sign': phrase['pause_mora']['vowel'],
                    'length': phrase['pause_mora']['vowel_length']
                })
                
        if post_len > 0.0:
            phonemes.append({'sign': 'pau', 'length': post_len})
            
        n = 10000000
        t = 0
        with lab_path.open('w', encoding='utf-8') as f:
            for p_item in phonemes:
                start = t
                end = t + int(p_item['length'] * n)
                f.write(f"{start} {end} {p_item['sign']}\n")
                t = end

    def apply_to_timeline(self):
        self.log_text.clear()
        if self.current_audio_query is None:
            self.add_error('先に「調声データ取得」を実行してください。')
            return

        out_dir_text = self.out_line_edit.text().strip()
        if not out_dir_text or not Path(out_dir_text).is_dir():
            self.add_error('有効な保存ディレクトリを設定してください。')
            return
        out_dir = Path(out_dir_text)

        resolve = self.fusion.GetResolve()
        projectManager = resolve.GetProjectManager()
        project = projectManager.GetCurrentProject()
        if project is None:
            self.add_error('ResolveのProjectが見付かりません。')
            return
        media_pool = project.GetMediaPool()
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            self.add_error('ResolveのTimelineが見付かりません。')
            return

        self.make_dropper_folder(project)

        # Get Resolve version for offset frame calculations
        version = resolve.GetVersion()
        ver19_1_or_above = (version[0] == 19 and version[1] == 1) or (version[0] >= 20)

        speaker_id = self.get_selected_speaker_id()
        idx = self.engine_combo.currentIndex()
        engine = self.engine_list[idx]
        speaker_display_name = self.speaker_combo.currentText()

        # Synthesis
        self.add_log('音声を合成中...')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            audio_bytes = synthesis(speaker_id, self.current_audio_query, max_retry=5, port=engine.port)
        except Exception as e:
            self.add_error(f'合成失敗: {e}')
            QApplication.restoreOverrideCursor()
            return
        QApplication.restoreOverrideCursor()

        # File Naming
        clean_speaker_name = re.sub(r'[\\/:*?"<>|()]+', '', speaker_display_name.split('(')[0])
        clean_text_preview = re.sub(r'[\\/:*?"<>|]+', '', self.text_edit.toPlainText().strip()[:5]).replace('\n', ' ')
        
        if self.edit_target is not None:
            # Overwrite Edit mode
            wav_file = self.edit_target['wav_path']
            json_file = self.edit_target['json_path']
            lab_file = wav_file.with_suffix('.lab')
        else:
            # New File mode: Calculate sequential index
            existing_indices = []
            for file_path in out_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() == '.wav':
                    parts = file_path.name.split('.')
                    if parts[0].isdigit():
                        existing_indices.append(int(parts[0]))
            next_idx = max(existing_indices) + 1 if existing_indices else 1
            filename_base = f"{next_idx:04d}.{clean_speaker_name}.{clean_text_preview}"
            wav_file = out_dir.joinpath(filename_base + '.wav')
            json_file = out_dir.joinpath(filename_base + '.json')
            lab_file = out_dir.joinpath(filename_base + '.lab')

        # Save files
        try:
            wav_file.write_bytes(audio_bytes)
            self.write_lab_file(lab_file, self.current_audio_query)
            
            # Save parameter JSON
            param_json = {
                'engine_name': engine.name,
                'speaker_display_name': speaker_display_name,
                'text': self.text_edit.toPlainText(),
                'basic_params': {
                    'speedScale': self.speed_spin.value(),
                    'intonationScale': self.intonation_spin.value(),
                    'volumeScale': self.volume_spin.value(),
                    'prePhonemeLength': self.pre_len_spin.value(),
                    'postPhonemeLength': self.post_len_spin.value()
                },
                'audio_query': self.current_audio_query
            }
            json_file.write_text(json.dumps(param_json, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            self.add_error(f'ファイル書き込み失敗: {e}')
            return

        # Prepare insertion metadata
        ch_data = chara_data.from_file(wav_file)
        
        # Determine track indices
        default_v_track = track_name2index(timeline, 'video', self.video_combo.currentText())
        default_a_track = track_name2index(timeline, 'audio', self.audio_combo.currentText())
        
        chara_v_index = track_name2index(timeline, 'video', ch_data.track_name + '_t')
        chara_a_index = track_name2index(timeline, 'audio', ch_data.track_name + '_a')
        
        video_index = chara_v_index if chara_v_index > 0 else default_v_track
        audio_index = chara_a_index if chara_a_index > 0 else default_a_track

        if video_index == 0 or audio_index == 0:
            self.add_error('適用するビデオトラックまたはオーディオトラックが見付かりません。設定を確認してください。')
            return

        fps = get_fps(timeline)
        
        # Calculate duration of the generated audio
        with wave.open(str(wav_file), 'rb') as wf:
            frames_count = wf.getnframes()
            rate = wf.getframerate()
            duration_sec = float(frames_count) / rate
        duration_frames = math.ceil(duration_sec * fps)

        # Retrieve text template
        root_folder = media_pool.GetRootFolder()
        dropper_folder = None
        text_plus_folder = None
        for folder in root_folder.GetSubFolderList():
            if folder.GetName() == 'VoiceDropper':
                dropper_folder = folder
                break
        if dropper_folder is not None:
            for folder in dropper_folder.GetSubFolderList():
                if folder.GetName() == self.text_plus_dir_name:
                    text_plus_folder = folder
                    break
        
        text_template = None
        if text_plus_folder is not None:
            for clip in text_plus_folder.GetClipList():
                if clip.GetClipProperty('Clip Name') == f'TextPlus{fps}FPS':
                    text_template = clip
                    break

        if text_template is None:
            self.add_error(f'テンプレートクリップ TextPlus{fps}FPS が見付かりません。')
            return

        resolve.OpenPage('edit')
        appender = Appender(resolve, media_pool)

        # Check / create folder for VOICEVOX Voice in MediaPool
        voice_folder = None
        for folder in root_folder.GetSubFolderList():
            if folder.GetName() == 'Voice':
                voice_folder = folder
                break
        if voice_folder is None:
            voice_folder = media_pool.AddSubFolder(root_folder, "Voice")
        
        # Start timeline update
        self.add_log('タイムラインへ追加・更新しています...')
        start_frame = get_currentframe(timeline)
        
        if self.edit_target is not None:
            # Replacement Mode: Remove old clips first
            start_frame = self.edit_target['start_frame']
            
            # Identify MediaPoolItem to delete later
            old_audio_item = self.edit_target['audio'].GetMediaPoolItem()
            
            # Delete clips from timeline
            timeline.DeleteClips([self.edit_target['audio'], self.edit_target['text']], False)
            
            # Delete old WAV from MediaPool to prevent duplicates
            if old_audio_item is not None:
                media_pool.DeleteClips([old_audio_item])

        # Import new WAV to MediaPool
        media_pool.SetCurrentFolder(voice_folder)
        mi = media_pool.ImportMedia(str(wav_file))
        if not mi or len(mi) == 0:
            self.add_error('WAVメディアのインポートに失敗しました。')
            return
        new_audio_media_item = mi[0]

        # Insert WAV to Timeline
        audio_info = {
            "mediaPoolItem": new_audio_media_item,
            "trackIndex": audio_index,
            "recordFrame": start_frame,
        }
        new_audio_clip = media_pool.AppendToTimeline([audio_info])[0]
        if new_audio_clip is None:
            self.add_error('タイムラインへの音声クリップ追加に失敗しました。')
            return

        # Insert Text+ to Timeline
        # ver19.1+ offset adjustment
        offset = 1 if ver19_1_or_above else 0
        new_text_clip = appender.append2timeline(
            item=text_template,
            duration=duration_frames + offset,
            track_index=video_index,
            record_frame=start_frame,
            media_type=1
        )
        if new_text_clip is None:
            self.add_error('タイムラインへの字幕クリップ追加に失敗しました。')
            return

        # Setup Text+ styled settings from template
        st_path_str = str(ch_data.setting_file)
        st = None
        if Path(st_path_str).is_file():
            st = ordered_dict_to_dict(bmd.readfile(st_path_str))
            
        if new_text_clip.GetFusionCompCount() > 0:
            comp = new_text_clip.GetFusionCompByIndex(1)
            comp.Lock()
            lst = list(comp.GetToolList(False, 'TextPlus').values())
            if len(lst) > 0:
                tool = lst[0]
                if st is not None:
                    tool.LoadSettings(st)
                # Apply generated text
                tool.StyledText = self.text_edit.toPlainText().strip()
                # Apply resolution settings
                tool.Width = int(timeline.GetSetting('timelineResolutionWidth'))
                tool.Height = int(timeline.GetSetting('timelineResolutionHeight'))
            comp.Unlock()

        self.add_log('タイムラインの適用が完了しました！')
        
        # Reset edit target context to allow consecutive insertions
        self.edit_target = None
        self.update_track()

    def read_from_timeline(self):
        self.log_text.clear()
        resolve = self.fusion.GetResolve()
        projectManager = resolve.GetProjectManager()
        project = projectManager.GetCurrentProject()
        if project is None:
            self.add_error('ResolveのProjectが見付かりません。')
            return
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            self.add_error('ResolveのTimelineが見付かりません。')
            return

        current_frame = get_currentframe(timeline)
        
        default_v_track = track_name2index(timeline, 'video', self.video_combo.currentText())
        default_a_track = track_name2index(timeline, 'audio', self.audio_combo.currentText())

        if default_v_track == 0 or default_a_track == 0:
            self.add_error('トラック設定（ビデオ、オーディオ）を正しく選択してください。')
            return

        self.add_log('タイムラインからプレイヘッド位置のクリップを検索中...')

        # Find audio clip under playhead
        audio_clip = get_item(timeline, 'audio', default_a_track, current_frame)
        text_clip = get_item(timeline, 'video', default_v_track, current_frame)

        if audio_clip is None:
            self.add_error('選択されたオーディオトラックの現在位置に音声クリップが見付かりません。')
            return

        # Retrieve file path
        mi = audio_clip.GetMediaPoolItem()
        if mi is None:
            self.add_error('メディアプールアイテムの取得に失敗しました。')
            return
        
        wav_path_str = mi.GetClipProperty('File Path')
        if not wav_path_str:
            self.add_error('クリップのファイルパスを取得できません。')
            return
        
        wav_path = Path(wav_path_str)
        json_path = wav_path.with_suffix('.json')

        if not json_path.is_file():
            self.add_error(f'対応する調声データファイルが見付かりません: {json_path.name}')
            return

        # Load json
        try:
            param_json = json.loads(json_path.read_text(encoding='utf-8'))
        except Exception as e:
            self.add_error(f'JSON読み込み失敗: {e}')
            return

        # Restore parameters
        self.add_log('調声データを復元しています...')
        engine_name = param_json.get('engine_name', 'VOICEVOX')
        speaker_display_name = param_json.get('speaker_display_name', '')
        text = param_json.get('text', '')
        basic_params = param_json.get('basic_params', {})
        audio_query_data = param_json.get('audio_query', None)

        # Restore Engine
        engine_idx = -1
        for idx, eng in enumerate(self.engine_list):
            if eng.name == engine_name:
                engine_idx = idx
                break
        if engine_idx != -1:
            self.engine_combo.setCurrentIndex(engine_idx)
            self.set_speaker_list()
            self.speaker_combo.setCurrentText(speaker_display_name)

        # Restore Sliders
        self.text_edit.setPlainText(text)
        self.speed_spin.setValue(basic_params.get('speedScale', 1.0))
        self.intonation_spin.setValue(basic_params.get('intonationScale', 1.0))
        self.volume_spin.setValue(basic_params.get('volumeScale', 1.0))
        self.pre_len_spin.setValue(basic_params.get('prePhonemeLength', 0.1))
        self.post_len_spin.setValue(basic_params.get('postPhonemeLength', 0.1))

        # Restore Mora Adjuster
        self.current_audio_query = audio_query_data
        self.mora_adjuster.set_query(self.current_audio_query)

        # Set edit context target
        self.edit_target = {
            'audio': audio_clip,
            'text': text_clip,
            'wav_path': wav_path,
            'json_path': json_path,
            'start_frame': audio_clip.GetStart()
        }

        self.add_log(f'復元完了: {wav_path.name} を編集しています。')

    def get_data(self) -> ConfigData:
        c = ConfigData()
        c.out_dir = self.out_line_edit.text()
        c.video_track = self.video_combo.currentText()
        c.audio_track = self.audio_combo.currentText()
        c.engine_index = self.engine_combo.currentIndex()
        c.speaker_display_name = self.speaker_combo.currentText()
        c.speed_scale = self.speed_spin.value()
        c.intonation_scale = self.intonation_spin.value()
        c.volume_scale = self.volume_spin.value()
        c.pre_phoneme_len = self.pre_len_spin.value()
        c.post_phoneme_len = self.post_len_spin.value()
        c.text = self.text_edit.toPlainText()
        return c

    def set_data(self, c: ConfigData):
        self.out_line_edit.setText(c.out_dir)
        self.video_combo.setCurrentText(c.video_track)
        self.audio_combo.setCurrentText(c.audio_track)
        self.engine_combo.setCurrentIndex(c.engine_index)
        self.set_speaker_list()
        self.speaker_combo.setCurrentText(c.speaker_display_name)
        self.speed_spin.setValue(c.speed_scale)
        self.intonation_spin.setValue(c.intonation_scale)
        self.volume_spin.setValue(c.volume_scale)
        self.pre_len_spin.setValue(c.pre_phoneme_len)
        self.post_len_spin.setValue(c.post_phoneme_len)
        self.text_edit.setPlainText(c.text)

    def load_config(self):
        c = ConfigData()
        if self.config_file.is_file():
            c.load(self.config_file)
        self.set_data(c)

    def save_config(self):
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        c = self.get_data()
        c.save(self.config_file)

    def closeEvent(self, event):
        self.save_config()
        self.stop()
        super().closeEvent(event)


# BMD Scripting helper
def ordered_dict_to_dict(d):
    # Fusion reads file as OrderedDict
    if isinstance(d, dict):
        return {k: ordered_dict_to_dict(v) for k, v in d.items()}
    elif isinstance(d, list):
        return [ordered_dict_to_dict(v) for v in d]
    else:
        return d


def run(fusion) -> None:
    # Set PySide environment styling
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(appearance.palette)
    app.setStyleSheet(appearance.stylesheet)

    window = MainWindow(fusion=fusion)
    window.show()
    sys.exit(app.exec())

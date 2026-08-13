# -*- coding: utf-8 -*-

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QScrollArea,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QLabel,
    QSlider,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QSizePolicy,
)

class MoraCard(QWidget):
    changed = Signal()

    def __init__(self, mora_dict, index, parent=None):
        super().__init__(parent)
        self.mora_dict = mora_dict
        self.index = index  # 0-indexed index within accent phrase
        
        self.base_pitch = float(mora_dict.get('pitch', 0.0))
        self.base_consonant_len = mora_dict.get('consonant_length')
        if self.base_consonant_len is not None:
            self.base_consonant_len = float(self.base_consonant_len)
        self.base_vowel_len = float(mora_dict.get('vowel_length', 0.1))

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        self.setFixedWidth(65)

        # 1. Accent Radio Button
        self.accent_radio = QRadioButton(self)
        self.accent_radio.setToolTip('アクセントの山')
        # Center the radio button
        self.accent_radio.setStyleSheet('margin-left: 20px;')
        layout.addWidget(self.accent_radio)

        # 2. Pitch Slider (Offset from base_pitch, range: ±1.5)
        self.pitch_label = QLabel('P: 0.0', self)
        self.pitch_label.setAlignment(Qt.AlignCenter)
        self.pitch_label.setStyleSheet('font-size: 9px; color: #aaa;')
        layout.addWidget(self.pitch_label)

        self.pitch_slider = QSlider(Qt.Vertical, self)
        self.pitch_slider.setMinimum(0)
        self.pitch_slider.setMaximum(300)
        self.pitch_slider.setValue(150)
        self.pitch_slider.setTickPosition(QSlider.TicksBelow)
        self.pitch_slider.setTickInterval(50)
        self.pitch_slider.setFixedHeight(60)
        
        # Calculate current slider value from current dict value
        current_pitch = float(self.mora_dict.get('pitch', 0.0))
        if self.base_pitch > 0.0:
            offset = current_pitch - self.base_pitch
            # clamp offset to -1.5 to +1.5
            offset = max(-1.5, min(1.5, offset))
            slider_val = int(150 + offset * 100)
            self.pitch_slider.setValue(slider_val)
            self.pitch_label.setText(f'P: {offset:+.2f}')
        else:
            self.pitch_slider.setEnabled(False)
            self.pitch_label.setText('P: ---')

        layout.addWidget(self.pitch_slider)

        # 3. Volume Slider (Range: 0.0 to 2.0, default 1.0)
        self.vol_label = QLabel('V: 1.0', self)
        self.vol_label.setAlignment(Qt.AlignCenter)
        self.vol_label.setStyleSheet('font-size: 9px; color: #aaa;')
        layout.addWidget(self.vol_label)

        self.vol_slider = QSlider(Qt.Vertical, self)
        self.vol_slider.setMinimum(0)
        self.vol_slider.setMaximum(200)
        self.vol_slider.setValue(100)
        self.vol_slider.setFixedHeight(50)
        
        current_vol = float(self.mora_dict.get('volume', 1.0))
        self.vol_slider.setValue(int(current_vol * 100))
        self.vol_label.setText(f'V: {current_vol:.1f}')
        layout.addWidget(self.vol_slider)

        # 4. Length Slider (Multiplier for vowel_length, range: 0.5 to 2.0)
        self.len_label = QLabel('L: 1.0', self)
        self.len_label.setAlignment(Qt.AlignCenter)
        self.len_label.setStyleSheet('font-size: 9px; color: #aaa;')
        layout.addWidget(self.len_label)

        self.len_slider = QSlider(Qt.Vertical, self)
        self.len_slider.setMinimum(50)
        self.len_slider.setMaximum(200)
        self.len_slider.setValue(100)
        self.len_slider.setFixedHeight(50)

        # We will adjust vowel_length and consonant_length proportionally
        # Based on vowel_length change multiplier
        current_vowel_len = float(self.mora_dict.get('vowel_length', 0.1))
        multiplier = current_vowel_len / self.base_vowel_len if self.base_vowel_len > 0 else 1.0
        multiplier = max(0.5, min(2.0, multiplier))
        self.len_slider.setValue(int(multiplier * 100))
        self.len_label.setText(f'L: {multiplier:.1f}')
        layout.addWidget(self.len_slider)

        # 5. Mora Text Label
        text = self.mora_dict.get('text', '')
        self.text_label = QLabel(text, self)
        self.text_label.setAlignment(Qt.AlignCenter)
        font = self.text_label.font()
        font.setBold(True)
        font.setPointSize(11)
        self.text_label.setFont(font)
        self.text_label.setStyleSheet('background-color: #2b2b2b; border-radius: 4px; padding: 2px;')
        layout.addWidget(self.text_label)

        # Event connections
        self.pitch_slider.valueChanged.connect(self.on_pitch_changed)
        self.vol_slider.valueChanged.connect(self.on_vol_changed)
        self.len_slider.valueChanged.connect(self.on_len_changed)

    def on_pitch_changed(self, val):
        if self.base_pitch <= 0.0:
            return
        offset = (val - 150) / 100.0
        self.mora_dict['pitch'] = self.base_pitch + offset
        self.pitch_label.setText(f'P: {offset:+.2f}')
        self.changed.emit()

    def on_vol_changed(self, val):
        vol = val / 100.0
        self.mora_dict['volume'] = vol
        self.vol_label.setText(f'V: {vol:.1f}')
        self.changed.emit()

    def on_len_changed(self, val):
        mult = val / 100.0
        self.mora_dict['vowel_length'] = self.base_vowel_len * mult
        if self.base_consonant_len is not None:
            self.mora_dict['consonant_length'] = self.base_consonant_len * mult
        self.len_label.setText(f'L: {mult:.1f}')
        self.changed.emit()


class AccentPhraseFrame(QFrame):
    changed = Signal()

    def __init__(self, phrase_dict, parent=None):
        super().__init__(parent)
        self.phrase_dict = phrase_dict
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)
        self.setStyleSheet('AccentPhraseFrame { border: 1px solid #444; border-radius: 6px; background-color: #222; }')
        
        self.mora_cards = []
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Header: Text & Interrogative Checkbox
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(2, 2, 2, 2)
        
        # Accent phrase representation
        text = ''.join([m.get('text', '') for m in self.phrase_dict.get('moras', [])])
        self.phrase_label = QLabel(text, self)
        font = self.phrase_label.font()
        font.setPointSize(9)
        self.phrase_label.setFont(font)
        header_layout.addWidget(self.phrase_label)
        
        header_layout.addStretch()

        # Interrogative
        self.interrogative_cb = QCheckBox('疑問文', self)
        self.interrogative_cb.setStyleSheet('font-size: 9px;')
        self.interrogative_cb.setChecked(bool(self.phrase_dict.get('is_interrogative', False)))
        self.interrogative_cb.stateChanged.connect(self.on_interrogative_changed)
        header_layout.addWidget(self.interrogative_cb)

        main_layout.addLayout(header_layout)

        # Moras Area
        self.moras_layout = QHBoxLayout()
        self.moras_layout.setContentsMargins(0, 0, 0, 0)
        self.moras_layout.setSpacing(2)

        # Button Group for Accent Radio Buttons
        self.accent_group = QButtonGroup(self)
        self.accent_group.idClicked.connect(self.on_accent_changed)

        moras = self.phrase_dict.get('moras', [])
        current_accent = int(self.phrase_dict.get('accent', 1))

        for idx, m_dict in enumerate(moras):
            card = MoraCard(m_dict, idx, self)
            card.changed.connect(self.changed.emit)
            self.moras_layout.addWidget(card)
            self.mora_cards.append(card)

            # Accent setup
            self.accent_group.addButton(card.accent_radio, idx + 1)  # 1-indexed for VOICEVOX accent
            if idx + 1 == current_accent:
                card.accent_radio.setChecked(True)

        main_layout.addLayout(self.moras_layout)

    def on_interrogative_changed(self, state):
        self.phrase_dict['is_interrogative'] = (state == Qt.Checked.value)
        self.changed.emit()

    def on_accent_changed(self, accent_id):
        self.phrase_dict['accent'] = accent_id
        self.changed.emit()


class MoraAdjuster(QScrollArea):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(230)

        self.container = QWidget(self)
        self.layout = QHBoxLayout(self.container)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)
        self.layout.setAlignment(Qt.AlignLeft)
        
        self.setWidget(self.container)
        self.audio_query = None

    def set_query(self, audio_query):
        self.audio_query = audio_query
        self.clear()

        if audio_query is None:
            return

        accent_phrases = audio_query.get('accent_phrases', [])
        for phrase in accent_phrases:
            frame = AccentPhraseFrame(phrase, self.container)
            frame.changed.connect(self.changed.emit)
            self.layout.addWidget(frame)

    def clear(self):
        # Remove old widgets
        while self.layout.count() > 0:
            item = self.layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

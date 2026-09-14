"""Manual truth and example category; recognition never supplies its own ground truth."""
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import QComboBox,QFormLayout,QGroupBox,QLabel,QLineEdit,QPushButton,QVBoxLayout
from audio.data import AudioDataError
from config.defaults import ORACLE_KEYS


class ReviewWidget(QGroupBox):
    save_requested=Signal()
    def __init__(self):
        super().__init__("評価用に正解を付けて保存")
        self.context={}
        layout=QVBoxLayout(self)
        form=QFormLayout()
        self.scope_combo=QComboBox();self.scope_combo.addItem("選択音（未選択なら全体）","selected");self.scope_combo.addItem("音声全体","whole")
        form.addRow("保存区間",self.scope_combo)
        self.mode_combo=QComboBox()
        for label,mode in (("Oracle単独","clip"),("連続イベント","events"),("VoGの両PASS","round")):self.mode_combo.addItem(label,mode)
        form.addRow("正解の単位",self.mode_combo)
        self.expected_edit=QLineEdit();self.expected_edit.setMaxLength(1000)
        self.expected_edit.setPlaceholderText("手動確認した L1,L2,MID… ／ Oracleがなければ「なし」")
        form.addRow("正解（手動確認）",self.expected_edit)
        self.category_combo=QComboBox()
        for label,kind in (("成功例","success"),("低confidence例","low_confidence"),("誤認識例","incorrect"),("戦闘音が重なる例","combat_noise")):self.category_combo.addItem(label,kind)
        form.addRow("記録の分類",self.category_combo)
        self.source_combo=QComboBox()
        for label,kind in (("未指定","unspecified"),("実戦の録音","gameplay"),("単独素材","isolated"),("合成・テスト","synthetic")):self.source_combo.addItem(label,kind)
        form.addRow("音声の由来",self.source_combo)
        self.notes_edit=QLineEdit();self.notes_edit.setMaxLength(2000);form.addRow("メモ",self.notes_edit)
        layout.addLayout(form)
        self.context_label=QLabel();self.context_label.setTextFormat(Qt.TextFormat.PlainText);self.context_label.setWordWrap(True);layout.addWidget(self.context_label)
        self.save_button=QPushButton("正解を付けて保存");layout.addWidget(self.save_button)
        self.save_button.clicked.connect(self.save_requested)
        self.set_available(False)

    def set_available(self,available):
        self.save_button.setEnabled(available)

    def set_context(self,context,incorrect=False):
        self.context=context
        self.category_combo.setCurrentIndex(0)
        self.source_combo.setCurrentIndex(0)
        self.scope_combo.setCurrentIndex(0)
        self.mode_combo.setCurrentIndex(0)
        self.notes_edit.clear()
        if incorrect:self.category_combo.setCurrentIndex(self.category_combo.findData("incorrect"))
        self.context_label.setText(context.get("description",""))
        self.expected_edit.clear()

    def annotation(self):
        value=self.expected_edit.text().strip().upper()
        if not value:raise AudioDataError("正解を手動で入力してください。Oracleがない音声は「なし」です。")
        expected=[] if value in ("なし","NONE","NO_ORACLE") else value.replace("、",",").replace("→",",").replace(" ",",").split(",")
        expected=[v for v in expected if v]
        if not expected and value not in ("なし","NONE","NO_ORACLE"):
            raise AudioDataError("正解を手動で入力してください。Oracleがない音声は「なし」です。")
        if any(v not in ORACLE_KEYS for v in expected):
            raise AudioDataError("正解はL1 / L2 / L3 / MID / R1 / R2 / R3で入力してください。")
        mode=self.mode_combo.currentData()
        if self.scope_combo.currentData()=="selected":mode="clip"
        if mode=="clip" and len(expected)>1:
            raise AudioDataError("選択音・単独音声の正解は1種類または「なし」です。")
        if mode=="round" and (not 3<=len(expected)<=7 or len(set(expected))!=len(expected)):
            raise AudioDataError("VoGラウンドの正解は重複のない3〜7種類です。")
        return {"mode":mode,"expected":expected,"category":self.category_combo.currentData(),
                "source_kind":self.source_combo.currentData(),"notes":self.notes_edit.text()}

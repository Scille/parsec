# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'forms/login_widget.ui'
#
# Created by: PyQt5 UI code generator 5.11.2
#
# WARNING! All changes made in this file will be lost!

from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_LoginWidget(object):
    def setupUi(self, LoginWidget):
        LoginWidget.setObjectName("LoginWidget")
        LoginWidget.resize(613, 544)
        sizePolicy = QtWidgets.QSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred
        )
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(LoginWidget.sizePolicy().hasHeightForWidth())
        LoginWidget.setSizePolicy(sizePolicy)
        self.verticalLayout = QtWidgets.QVBoxLayout(LoginWidget)
        self.verticalLayout.setContentsMargins(5, 5, 5, 5)
        self.verticalLayout.setSpacing(5)
        self.verticalLayout.setObjectName("verticalLayout")
        self.group_login = QtWidgets.QGroupBox(LoginWidget)
        self.group_login.setObjectName("group_login")
        self.verticalLayout_2 = QtWidgets.QVBoxLayout(self.group_login)
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.combo_devices = QtWidgets.QComboBox(self.group_login)
        self.combo_devices.setObjectName("combo_devices")
        self.verticalLayout_2.addWidget(self.combo_devices)
        self.line_edit_password = QtWidgets.QLineEdit(self.group_login)
        self.line_edit_password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.line_edit_password.setObjectName("line_edit_password")
        self.verticalLayout_2.addWidget(self.line_edit_password)
        self.horizontalLayout_3 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_3.setObjectName("horizontalLayout_3")
        spacerItem = QtWidgets.QSpacerItem(
            40, 20, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum
        )
        self.horizontalLayout_3.addItem(spacerItem)
        self.button_login = QtWidgets.QPushButton(self.group_login)
        self.button_login.setEnabled(True)
        self.button_login.setObjectName("button_login")
        self.horizontalLayout_3.addWidget(self.button_login)
        self.verticalLayout_2.addLayout(self.horizontalLayout_3)
        self.label_error = QtWidgets.QLabel(self.group_login)
        font = QtGui.QFont()
        font.setPointSize(12)
        font.setBold(True)
        font.setWeight(75)
        self.label_error.setFont(font)
        self.label_error.setText("")
        self.label_error.setAlignment(QtCore.Qt.AlignCenter)
        self.label_error.setObjectName("label_error")
        self.verticalLayout_2.addWidget(self.label_error)
        self.verticalLayout.addWidget(self.group_login)
        self.group_claim = QtWidgets.QGroupBox(LoginWidget)
        self.group_claim.setObjectName("group_claim")
        self.verticalLayout_3 = QtWidgets.QVBoxLayout(self.group_claim)
        self.verticalLayout_3.setObjectName("verticalLayout_3")
        self.label = QtWidgets.QLabel(self.group_claim)
        self.label.setObjectName("label")
        self.verticalLayout_3.addWidget(self.label)
        self.line_edit_claim_login = QtWidgets.QLineEdit(self.group_claim)
        self.line_edit_claim_login.setText("")
        self.line_edit_claim_login.setObjectName("line_edit_claim_login")
        self.verticalLayout_3.addWidget(self.line_edit_claim_login)
        self.line_edit_claim_password = QtWidgets.QLineEdit(self.group_claim)
        self.line_edit_claim_password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.line_edit_claim_password.setObjectName("line_edit_claim_password")
        self.verticalLayout_3.addWidget(self.line_edit_claim_password)
        self.line_edit_claim_password_check = QtWidgets.QLineEdit(self.group_claim)
        self.line_edit_claim_password_check.setEchoMode(QtWidgets.QLineEdit.Password)
        self.line_edit_claim_password_check.setObjectName("line_edit_claim_password_check")
        self.verticalLayout_3.addWidget(self.line_edit_claim_password_check)
        self.line_edit_claim_device = QtWidgets.QLineEdit(self.group_claim)
        self.line_edit_claim_device.setObjectName("line_edit_claim_device")
        self.verticalLayout_3.addWidget(self.line_edit_claim_device)
        self.line_edit_claim_token = QtWidgets.QLineEdit(self.group_claim)
        self.line_edit_claim_token.setObjectName("line_edit_claim_token")
        self.verticalLayout_3.addWidget(self.line_edit_claim_token)
        self.horizontalLayout_4 = QtWidgets.QHBoxLayout()
        self.horizontalLayout_4.setObjectName("horizontalLayout_4")
        spacerItem1 = QtWidgets.QSpacerItem(
            40, 20, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum
        )
        self.horizontalLayout_4.addItem(spacerItem1)
        self.button_claim = QtWidgets.QPushButton(self.group_claim)
        self.button_claim.setEnabled(False)
        self.button_claim.setObjectName("button_claim")
        self.horizontalLayout_4.addWidget(self.button_claim)
        self.verticalLayout_3.addLayout(self.horizontalLayout_4)
        self.label_claim_error = QtWidgets.QLabel(self.group_claim)
        font = QtGui.QFont()
        font.setPointSize(12)
        font.setBold(True)
        font.setWeight(75)
        self.label_claim_error.setFont(font)
        self.label_claim_error.setText("")
        self.label_claim_error.setAlignment(QtCore.Qt.AlignCenter)
        self.label_claim_error.setObjectName("label_claim_error")
        self.verticalLayout_3.addWidget(self.label_claim_error)
        self.verticalLayout.addWidget(self.group_claim)
        self.button_login_instead = QtWidgets.QCommandLinkButton(LoginWidget)
        self.button_login_instead.setObjectName("button_login_instead")
        self.verticalLayout.addWidget(self.button_login_instead)
        self.button_claim_instead = QtWidgets.QCommandLinkButton(LoginWidget)
        self.button_claim_instead.setObjectName("button_claim_instead")
        self.verticalLayout.addWidget(self.button_claim_instead)
        spacerItem2 = QtWidgets.QSpacerItem(
            20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding
        )
        self.verticalLayout.addItem(spacerItem2)

        self.retranslateUi(LoginWidget)
        self.button_login_instead.clicked.connect(self.group_login.show)
        self.button_login_instead.clicked.connect(self.group_claim.hide)
        self.button_claim_instead.clicked.connect(self.group_claim.show)
        self.button_claim_instead.clicked.connect(self.group_login.hide)
        self.button_login_instead.clicked.connect(self.button_claim_instead.show)
        self.button_claim_instead.clicked.connect(self.button_login_instead.show)
        self.button_claim_instead.clicked.connect(self.button_claim_instead.hide)
        self.button_login_instead.clicked.connect(self.button_login_instead.hide)
        QtCore.QMetaObject.connectSlotsByName(LoginWidget)

    def retranslateUi(self, LoginWidget):
        _translate = QtCore.QCoreApplication.translate
        LoginWidget.setWindowTitle(_translate("LoginWidget", "Form"))
        self.group_login.setTitle(_translate("LoginWidget", "Log In"))
        self.line_edit_password.setPlaceholderText(_translate("LoginWidget", "Password"))
        self.button_login.setText(_translate("LoginWidget", "Log In"))
        self.group_claim.setTitle(_translate("LoginWidget", "Register a new account"))
        self.label.setText(
            _translate(
                "LoginWidget",
                "To register, you need another user to create an account and get a token.",
            )
        )
        self.line_edit_claim_login.setPlaceholderText(_translate("LoginWidget", "Login"))
        self.line_edit_claim_password.setPlaceholderText(_translate("LoginWidget", "Password"))
        self.line_edit_claim_password_check.setPlaceholderText(
            _translate("LoginWidget", "Password check")
        )
        self.line_edit_claim_device.setPlaceholderText(_translate("LoginWidget", "Device"))
        self.line_edit_claim_token.setPlaceholderText(_translate("LoginWidget", "Token"))
        self.button_claim.setText(_translate("LoginWidget", "Register"))
        self.button_login_instead.setText(_translate("LoginWidget", "Log In instead"))
        self.button_claim_instead.setText(
            _translate("LoginWidget", "Register a new account instead")
        )

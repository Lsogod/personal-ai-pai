import { Ionicons } from "@expo/vector-icons";
import { PropsWithChildren } from "react";
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors, radii } from "../design/tokens";

type PanelModalProps = PropsWithChildren<{
  visible: boolean;
  title: string;
  onClose: () => void;
}>;

export function PanelModal({ visible, title, onClose, children }: PanelModalProps) {
  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onClose}
    >
      <SafeAreaView style={styles.safeArea} edges={["top", "bottom"]}>
        <View style={styles.header}>
          <Pressable style={styles.closeBtn} onPress={onClose}>
            <Ionicons name="chevron-back" size={20} color={colors.text2} />
            <Text style={styles.closeText}>返回</Text>
          </Pressable>
          <Text style={styles.title}>{title}</Text>
          <View style={styles.placeholder} />
        </View>

        <ScrollView
          style={styles.body}
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {children}
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  header: {
    minHeight: 58,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 18,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
    backgroundColor: "rgba(255,255,255,0.96)",
  },
  closeBtn: {
    minWidth: 58,
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  closeText: {
    fontSize: 14,
    fontWeight: "700",
    color: colors.text2,
  },
  title: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
  },
  placeholder: {
    width: 58,
  },
  body: {
    flex: 1,
  },
  content: {
    paddingHorizontal: 18,
    paddingVertical: 18,
    gap: 14,
  },
});

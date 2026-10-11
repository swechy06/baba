package tr.baba.ticket;

import com.fasterxml.jackson.databind.node.ObjectNode;

/** The stable keys keep the existing Python ticket_data.json compatible. */
public enum SupportType {
    GAMEPASS("gamepass_destek", "Gamepass Destek", "Gamepass Destek Bileti"),
    GAME("oyun_destek", "Oyun Destek", "Oyun Destek Bileti"),
    RANK("rutbe_destek", "Rütbe Destek", "Rütbe Destek Bileti"),
    ADVERTISING("reklam_destek", "Reklam Destek", "Reklam Destek Bileti"),
    TRANSFER("transfer", "Transfer", "Transfer Bileti"),
    MODERATOR("moderator", "Moderatör", "Moderatör Bileti");

    private final String key;
    private final String label;
    private final String panelLabel;

    SupportType(String key, String label, String panelLabel) {
        this.key = key;
        this.label = label;
        this.panelLabel = panelLabel;
    }

    public String key() {
        return key;
    }

    public String label() {
        return label;
    }

    public String panelLabel() {
        return panelLabel;
    }

    public static SupportType fromKey(String key) {
        for (SupportType type : values()) {
            if (type.key.equals(key)) {
                return type;
            }
        }
        return null;
    }

    public static ObjectNode defaultGuild() {
        ObjectNode guild = ConfigStore.MAPPER.createObjectNode();
        guild.putNull("panel_channel");
        guild.putNull("panel_message_id");
        ObjectNode supportTypes = guild.putObject("support_types");
        for (SupportType type : values()) {
            ObjectNode settings = supportTypes.putObject(type.key);
            settings.putNull("open_category");
            settings.putNull("closed_category");
            settings.putArray("roles");
            settings.putNull("log_channel");
        }
        return guild;
    }
}

<#import "template.ftl" as layout>
<@layout.emailLayout>
${kcSanitize(msg("emailUserEnabledBodyHtml"))?no_esc}
</@layout.emailLayout>